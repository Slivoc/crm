# In routes/salespeople.py
import json
from collections import defaultdict
from datetime import datetime, date, timedelta
from math import log1p
import re
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, Response, stream_with_context
from routes.auth import login_required, current_user
from ai_helper import get_cached_news, get_top_customers_for_news, get_watched_customers_for_news, get_cache_key, cleanup_old_cache_files, fetch_customer_news_perplexity, process_customer_news_chatgpt, cache_news, filter_duplicate_news, store_sent_news_items
from routes.news_email import get_news_email_addresses, send_news_email
from models import (get_salespeople, get_all_salespeople_with_contact_counts, get_call_list_contact_ids, add_to_call_list, remove_from_call_list,
    snooze_call_list_entry, get_call_list_with_communication_status, update_call_list_priority, update_call_list_notes, bulk_add_to_call_list, get_salesperson_recent_communications, get_communication_types_for_salesperson, delete_customer_tag, insert_customer_tags, get_all_tags, insert_customer_tag, get_engagement_settings, get_all_salespeople_with_customer_counts, get_priorities, save_engagement_settings, insert_salesperson, get_active_salespeople, get_engagement_metrics, toggle_salesperson_active, get_customer_contacts_with_communications, update_customer_field_value, get_all_contact_statuses, get_status_counts_for_salesperson, get_tags_by_customer_id, get_salesperson_customers_with_spend, get_salesperson_by_id, get_salesperson_contacts, get_contact_communications, get_salesperson_sales_by_date_range, get_salesperson_monthly_sales, get_accounts_monthly_sales,
                    update_salesperson, delete_salesperson, get_template_by_id,
                    get_customers_with_status_and_updates, get_customer_status_options, get_consolidated_customer_orders, get_consolidated_customer_ids,
                    add_customer_status_update, get_customer_updates, get_customer_orders_by_date_range, get_customer_active_orders_count,
                    get_customer_orders, Permission, get_salespeople_with_stats, get_total_customers, get_total_orders, get_total_active_orders, get_salesperson_recent_activities, get_salesperson_customers, get_salesperson_pending_orders, get_customer_by_id,
                    get_company_types_by_customer_id)
from db import get_db_connection, execute as db_execute, db_cursor, _using_postgres, _execute_with_cursor

from dateutil.relativedelta import relativedelta
import calendar
from openai import OpenAI

salespeople_bp = Blueprint('salespeople', __name__)

# PostgreSQL migration helpers
def _execute_with_cursor(cursor, query, params=None):
    """Execute a query with automatic placeholder translation for Postgres"""
    if _using_postgres():
        # Translate ? placeholders to %s for Postgres
        query = query.replace('?', '%s')
    cursor.execute(query, params or ())
    return cursor

def is_mobile():
    user_agent = request.headers.get('User-Agent', '').lower()
    mobile_keywords = ['mobile', 'android', 'iphone', 'ipad']
    return any(keyword in user_agent for keyword in mobile_keywords)

_openai_email_client = None


def _get_openai_email_client():
    """Lazily instantiate the OpenAI client for outreach suggestions."""
    global _openai_email_client
    if _openai_email_client is None:
        _openai_email_client = OpenAI(api_key=current_app.config.get('OPENAI_API_KEY') or None)
    return _openai_email_client


def _parse_datetime_value(value):
    """Normalize datetime values from the database into datetime objects."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='ignore')
    if isinstance(value, str):
        cleaned = value.replace('Z', '+00:00')
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError:
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M'):
                try:
                    return datetime.strptime(cleaned, fmt)
                except ValueError:
                    continue
    return None


def _month_start(reference_date, months_ago=0):
    """Return the first day of the month N months before reference_date."""
    return reference_date.replace(day=1) - relativedelta(months=months_ago)


def _strip_html(value: str) -> str:
    if not value:
        return ''
    return re.sub(r'<[^>]+>', '', value)


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


def _get_customer_communication_snapshots(customer_groups, salesperson_id):
    """Fetch latest communications (any + email) for consolidated customer groups."""
    if not customer_groups:
        return {}

    child_to_main = {}
    for main_id, related_ids in customer_groups.items():
        for cid in related_ids:
            child_to_main[cid] = main_id

    all_customer_ids = list(child_to_main.keys())
    placeholders = ','.join('?' for _ in all_customer_ids)

    query = f"""
        SELECT 
            cc.customer_id,
            cc.contact_id,
            cc.communication_type,
            cc.date,
            cc.notes,
            cc.email_message_id,
            cc.email_direction,
            c.name as contact_name,
            c.email as contact_email
        FROM contact_communications cc
        LEFT JOIN contacts c ON cc.contact_id = c.id
        WHERE cc.customer_id IN ({placeholders})
          AND cc.salesperson_id = ?
        ORDER BY cc.date DESC
    """

    rows = db_execute(query, all_customer_ids + [salesperson_id], fetch='all') or []
    rows = [dict(row) for row in rows if row]
    snapshots = {}

    for row in rows:
        customer_id = row.get('customer_id')
        if not customer_id:
            continue
        main_id = child_to_main.get(customer_id, customer_id)
        snapshots.setdefault(main_id, {'last_contact': None, 'last_email': None})

        parsed_date = _parse_datetime_value(row.get('date'))
        contact_payload = {
            'contact_name': row.get('contact_name'),
            'contact_email': row.get('contact_email'),
            'notes': row.get('notes'),
            'type': row.get('communication_type'),
            'direction': row.get('email_direction'),
            'message_id': row.get('email_message_id'),
            'datetime': parsed_date,
            'date': parsed_date.isoformat() if parsed_date else None
        }

        if snapshots[main_id]['last_contact'] is None:
            snapshots[main_id]['last_contact'] = contact_payload

        if row.get('communication_type') == 'email' and snapshots[main_id]['last_email'] is None:
            snapshots[main_id]['last_email'] = contact_payload

    return snapshots


def _get_email_preview_from_cache(message_id, graph_user_id):
    """Retrieve the subject/body preview for a cached Graph email."""
    if not (message_id and graph_user_id):
        return None

    row = db_execute(
        """
        SELECT raw_message, body_preview, subject 
        FROM graph_email_cache 
        WHERE user_id = ? AND message_id = ? 
        LIMIT 1
        """,
        (graph_user_id, message_id),
        fetch="one"
    )

    if not row:
        return None

    raw_message = row.get('raw_message')
    if isinstance(raw_message, (bytes, bytearray)):
        raw_message = raw_message.decode('utf-8', errors='ignore')
    if isinstance(raw_message, str):
        try:
            raw_message = json.loads(raw_message)
        except json.JSONDecodeError:
            raw_message = None

    body_content = None
    if isinstance(raw_message, dict):
        body_data = raw_message.get('body') or raw_message.get('Body') or {}
        body_content = body_data.get('content') if isinstance(body_data, dict) else None
        if not body_content:
            body_content = raw_message.get('bodyPreview') or raw_message.get('BodyPreview')

    preview = body_content or row.get('body_preview') or ''
    subject = row.get('subject') or ''
    return {
        'subject': subject.strip(),
        'preview': _strip_html(preview).strip()[:800]
    }


def _extract_graph_emails(value):
    """Extract email addresses from Graph cache JSON fields."""
    if not value:
        return []
    if isinstance(value, (bytes, bytearray)):
        value = value.decode('utf-8', errors='ignore')
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return [value] if '@' in value else []
    if isinstance(value, dict):
        value = value.get('emailAddress', value)
        if isinstance(value, dict):
            addr = value.get('address') or value.get('email')
            return [addr] if addr else []
        return []
    if isinstance(value, list):
        emails = []
        for item in value:
            emails.extend(_extract_graph_emails(item))
        return emails
    return []


def _get_latest_graph_emails_for_customers(customer_groups, graph_user_id, limit=200):
    """Return the latest Graph email per main customer based on contact emails."""
    if not graph_user_id or not customer_groups:
        return {}

    child_to_main = {}
    all_customer_ids = []
    for main_id, related_ids in customer_groups.items():
        for cid in related_ids:
            child_to_main[cid] = main_id
            all_customer_ids.append(cid)

    if not all_customer_ids:
        return {}

    placeholders = ','.join('?' for _ in all_customer_ids)
    contacts = db_execute(
        f"""
        SELECT customer_id, email
        FROM contacts
        WHERE email IS NOT NULL AND email != ''
          AND customer_id IN ({placeholders})
        """,
        all_customer_ids,
        fetch='all'
    ) or []

    email_to_main = {}
    for row in (dict(r) for r in contacts if r):
        email = (row.get('email') or '').strip().lower()
        if not email:
            continue
        main_id = child_to_main.get(row.get('customer_id'))
        if main_id:
            email_to_main[email] = main_id

    if not email_to_main:
        return {}

    rows = db_execute(
        """
        SELECT message_id, subject, body_preview, sender_name, sender_email,
               received_datetime, sent_datetime, to_recipients, cc_recipients, raw_message
        FROM graph_email_cache
        WHERE user_id = ?
        ORDER BY COALESCE(received_datetime, sent_datetime) DESC
        LIMIT ?
        """,
        (graph_user_id, limit),
        fetch='all'
    ) or []

    latest_by_main = {}
    for row in (dict(r) for r in rows if r):
        sender_email = (row.get('sender_email') or '').strip().lower()
        recipient_emails = []
        recipient_emails.extend(_extract_graph_emails(row.get('to_recipients')))
        recipient_emails.extend(_extract_graph_emails(row.get('cc_recipients')))
        recipient_emails = [e.strip().lower() for e in recipient_emails if e]

        possible_emails = []
        if sender_email:
            possible_emails.append(sender_email)
        possible_emails.extend(recipient_emails)

        match_main = None
        for email in possible_emails:
            match_main = email_to_main.get(email)
            if match_main:
                break

        if not match_main or match_main in latest_by_main:
            continue

        raw_message = row.get('raw_message')
        if isinstance(raw_message, (bytes, bytearray)):
            raw_message = raw_message.decode('utf-8', errors='ignore')
        if isinstance(raw_message, str):
            try:
                raw_message = json.loads(raw_message)
            except json.JSONDecodeError:
                raw_message = None

        body_content = None
        if isinstance(raw_message, dict):
            body_data = raw_message.get('body') or raw_message.get('Body') or {}
            body_content = body_data.get('content') if isinstance(body_data, dict) else None
            if not body_content:
                body_content = raw_message.get('bodyPreview') or raw_message.get('BodyPreview')

        preview = body_content or row.get('body_preview') or ''
        subject = row.get('subject') or ''
        sent_at = row.get('received_datetime') or row.get('sent_datetime')
        sent_dt = _parse_datetime_value(sent_at)

        latest_by_main[match_main] = {
            'message_id': row.get('message_id'),
            'subject': subject.strip(),
            'preview': _strip_html(preview).strip()[:800],
            'sender_email': row.get('sender_email'),
            'sender_name': row.get('sender_name'),
            'date': sent_dt.isoformat() if sent_dt else None
        }

    return latest_by_main


def _get_contact_emails_for_customers(customer_groups):
    """Return contact email lists keyed by main customer id."""
    if not customer_groups:
        return {}

    child_to_main = {}
    all_customer_ids = []
    for main_id, related_ids in customer_groups.items():
        for cid in related_ids:
            child_to_main[cid] = main_id
            all_customer_ids.append(cid)

    if not all_customer_ids:
        return {}

    placeholders = ','.join('?' for _ in all_customer_ids)
    rows = db_execute(
        f"""
        SELECT id, customer_id, name, second_name, email, job_title
        FROM contacts
        WHERE email IS NOT NULL AND email != ''
          AND customer_id IN ({placeholders})
        ORDER BY name, second_name
        """,
        all_customer_ids,
        fetch='all'
    ) or []

    contacts_by_main = {}
    for row in (dict(r) for r in rows if r):
        main_id = child_to_main.get(row.get('customer_id'))
        if not main_id:
            continue
        full_name = f"{row.get('name') or ''} {row.get('second_name') or ''}".strip()
        contacts_by_main.setdefault(main_id, []).append({
            'id': row.get('id'),
            'name': full_name or row.get('email'),
            'email': row.get('email'),
            'job_title': row.get('job_title')
        })

    return contacts_by_main


def _get_contact_counts_for_customers(customer_groups):
    """Return total contact counts keyed by main customer id."""
    if not customer_groups:
        return {}

    child_to_main = {}
    all_customer_ids = []
    for main_id, related_ids in customer_groups.items():
        for cid in related_ids:
            child_to_main[cid] = main_id
            all_customer_ids.append(cid)

    if not all_customer_ids:
        return {}

    placeholders = ','.join('?' for _ in all_customer_ids)
    rows = db_execute(
        f"""
        SELECT customer_id, COUNT(*) AS contact_count
        FROM contacts
        WHERE customer_id IN ({placeholders})
        GROUP BY customer_id
        """,
        all_customer_ids,
        fetch='all'
    ) or []

    counts_by_main = defaultdict(int)
    for row in (dict(r) for r in rows if r):
        main_id = child_to_main.get(row.get('customer_id'))
        if not main_id:
            continue
        counts_by_main[main_id] += int(row.get('contact_count') or 0)

    return dict(counts_by_main)


def _filter_customers_with_contact_emails(customers):
    """Filter customers to those with at least one contact email across related companies."""
    if not customers:
        return [], {}, {}

    customer_groups = {
        customer['id']: customer.get('related_customer_ids') or [customer['id']]
        for customer in customers
        if customer.get('id')
    }

    contacts_by_main = _get_contact_emails_for_customers(customer_groups)
    eligible_main_ids = set(contacts_by_main.keys())

    filtered_customers = [
        customer for customer in customers
        if customer and customer.get('id') in eligible_main_ids
    ]
    filtered_groups = {
        main_id: customer_groups[main_id]
        for main_id in eligible_main_ids
        if main_id in customer_groups
    }
    return filtered_customers, filtered_groups, contacts_by_main


def _build_news_lookup_for_customers(salesperson_id):
    """Return cached news indexed by lowercase customer name."""
    cached = get_cached_news(get_cache_key(salesperson_id)) or {}
    news_map = {}
    for item in cached.get('news_items', []) or []:
        key = (item.get('customer_name') or '').lower().strip()
        if not key:
            continue
        news_map.setdefault(key, []).append(item)
    return news_map, bool(cached.get('news_items'))


def _hydrate_news_for_customers(customers, news_map, max_fetch=3):
    """Fetch fresh news for customers missing cached items (limited for performance)."""
    fetched = 0
    for customer in customers:
        key = customer['name'].lower()
        if key in news_map or fetched >= max_fetch:
            continue
        try:
            raw_news = fetch_customer_news_perplexity(customer)
            if not raw_news:
                continue
            processed = process_customer_news_chatgpt(customer, raw_news)
            if processed and processed.get('news_items'):
                news_map[key] = processed['news_items']
                fetched += 1
        except Exception as exc:
            print(f"News fetch failed for {customer['name']}: {exc}")
            continue
    return fetched


def _compute_suggestion_score(customer, comm_info):
    """Weighted score to prioritize high-value, stale zero-spend customers.

    For MRO companies, mro_score (1-100) is used instead of fleet_size.
    For operators or companies with both, both metrics contribute.
    """
    est_revenue = float(customer.get('estimated_revenue') or 0)
    fleet_size = float(customer.get('fleet_size') or 0)
    mro_score = float(customer.get('mro_score') or 0)

    last_contact_dt = None
    if comm_info and comm_info.get('last_contact', {}).get('datetime'):
        last_contact_dt = comm_info['last_contact']['datetime']
    days_since_contact = None
    if last_contact_dt:
        days_since_contact = max((datetime.utcnow() - last_contact_dt).days, 0)

    revenue_component = min(log1p(est_revenue) / log1p(500000 + 1), 1.0)
    fleet_component = min(log1p(fleet_size) / log1p(400 + 1), 1.0)
    # MRO score is already 0-100, normalize to 0-1
    mro_component = min(mro_score / 100, 1.0)
    recency_component = 1.0 if days_since_contact is None else min(days_since_contact, 180) / 180

    # Use the higher of fleet_component or mro_component for the "capability" score
    # This ensures MRO companies get proper weighting even without a fleet
    capability_component = max(fleet_component, mro_component)

    raw_score = (
        revenue_component * 0.45 +
        capability_component * 0.2 +
        recency_component * 0.35
    )

    score = round(min(raw_score, 1.3) * 100, 1)
    return score, {
        'revenue_component': round(revenue_component, 3),
        'fleet_component': round(fleet_component, 3),
        'mro_component': round(mro_component, 3),
        'capability_component': round(capability_component, 3),
        'recency_component': round(recency_component, 3),
        'days_since_contact': days_since_contact
    }


def _generate_email_suggestion(customer, comm_info, news_items, last_email_preview, seed_template=None, last_email_body=None):
    """Use OpenAI to draft the next outreach email with contextual cues."""
    client = _get_openai_email_client()

    last_email_summary = None
    if last_email_preview or last_email_body:
        last_email_summary = {
            'subject': (last_email_preview or {}).get('subject') if isinstance(last_email_preview, dict) else None,
            'preview': (last_email_preview or {}).get('preview') if isinstance(last_email_preview, dict) else None,
            'body': (last_email_body or '').strip() if last_email_body else None
        }

    news_snippets = []
    for item in news_items[:2]:
        headline = item.get('headline') or ''
        summary = item.get('summary') or ''
        news_snippets.append(f"{headline}: {summary}")

    payload = {
        'customer_name': customer.get('name'),
        'estimated_revenue': customer.get('estimated_revenue') or 0,
        'fleet_size': customer.get('fleet_size') or 0,
        'customer_status': customer.get('customer_status'),
        'latest_update': customer.get('latest_update'),
        'last_contact': comm_info.get('last_contact') if comm_info else None,
        'news_snippets': news_snippets,
        'last_email': last_email_summary,
        'seed_template': seed_template or ''
    }

    system_prompt = (
        "You are a warm, helpful account manager writing like a real person. "
        "We supply approved, traceable fasteners/consumables/hardware that operators, OEMs, and MROs need. "
        "Suggest the next email for a customer. "
        "Return ONLY valid JSON with keys 'subject' and 'body'. "
        "If last_email.body is provided, treat it as the most recent email thread "
        "and draft a relevant follow-up that references prior context. "
        "Write as if you already know the person: friendly, calm, and familiar, not salesy. "
        "Keep the body under 120 words, use plain language, and propose a clear next step. "
        "Avoid salesy buzzwords, hype, or exaggerated claims. "
        "Avoid stiff opener cliches like \"I hope this message finds you well\". "
        "Avoid em dashes (—) and double hyphens (--). Use commas or full stops instead. "
        "Do not overpraise or gush. "
        "If you mention news, keep it short and casual, avoid long place names or exact locations, "
        "and avoid using the full formal company name if it feels unnatural. "
        "Avoid email cliches like \"touch base\", \"circle back\", \"reach out\", "
        "\"see how things are going\", or \"hope you're well\". "
        "Never use filler or jargon like \"synergy\", \"synergies\", \"venture\", \"ventures\", "
        "\"leverage\", \"unlock\", \"optimize\", \"streamline\", \"world-class\", "
        "\"cutting-edge\", or \"value proposition\". "
        "Do not include placeholders like [Your Name], [Your Company], or bracketed fields. "
        "If no contact name is available, use a neutral greeting like \"Hi there\" "
        "or \"Hi {customer_name} team\". "
        "Use a simple sign-off (e.g., \"Thanks,\" or \"Best,\") with no contact details. "
        "If a seed_template is provided, use it as the base structure and tone."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-5.2-chat-latest",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, default=str)}
            ],
            temperature=0.4,
            max_tokens=320,

            # Optional (only if you want extra control and your SDK supports these params):
            # verbosity="low",
            # reasoning_effort="minimal",
        )

        content = response.choices[0].message.content.strip()
        if content.startswith('```'):
            parts = content.split('```')
            content = parts[1] if len(parts) > 1 else content
            if content.startswith('json'):
                content = content[4:]
        suggestion = json.loads(content)
        subject = suggestion.get('subject', '').strip()
        body = _strip_ai_placeholders(suggestion.get('body', '').strip())
        return {
            'subject': subject,
            'body': body,
            'source': 'openai'
        }
    except Exception as exc:
        print(f"Email suggestion failed for {customer.get('name')}: {exc}")
        fallback_subject = f"Exploring opportunities with {customer.get('name')}"
        news_hook = f" I noticed {news_snippets[0]}" if news_snippets else ""
        fallback_body = (
            f"Hi team,\n\n"
            f"We haven't worked together yet, and I'd love to understand your upcoming needs."
            f"{news_hook}\n\n"
            "Could we schedule a quick call this week to map out where we can help?"
        )
        return {
            'subject': fallback_subject,
            'body': fallback_body,
            'source': 'fallback'
        }



def _build_contact_comm_snapshot(communications):
    """Build a lightweight communication snapshot for a single contact."""
    if not communications:
        return {}

    snapshot = {'last_contact': None, 'last_email': None}
    for idx, comm in enumerate(communications):
        comm_type = (comm.get('communication_type') or '').lower()
        payload = {
            'type': comm.get('communication_type'),
            'notes': comm.get('notes'),
            'datetime': comm.get('date'),
            'message_id': comm.get('email_message_id')
        }
        if idx == 0:
            snapshot['last_contact'] = payload
        if comm_type == 'email' and snapshot['last_email'] is None:
            snapshot['last_email'] = payload
        if snapshot['last_contact'] and snapshot['last_email']:
            break

    return snapshot


def _get_customer_outreach_profile(customer_id):
    """Fetch customer fields needed for outreach suggestions and news hints."""
    row = db_execute(
        """
        SELECT 
            c.id,
            c.name,
            c.description,
            c.country,
            c.website,
            c.fleet_size,
            c.estimated_revenue,
            c.salesperson_id,
            cs.status AS customer_status,
            (
                SELECT update_text
                FROM customer_updates cu
                WHERE cu.customer_id = c.id
                ORDER BY cu.date DESC
                LIMIT 1
            ) AS latest_update
        FROM customers c
        LEFT JOIN customer_status cs ON c.status_id = cs.id
        WHERE c.id = ?
        """,
        (customer_id,),
        fetch='one'
    )
    return dict(row) if row else None


def _generate_contact_email_suggestion(contact, customer, comm_info, news_items, last_email_preview, last_email_body=None):
    """Use OpenAI to draft the next email to a specific contact."""
    client = _get_openai_email_client()
    contact_name = f"{contact.get('name') or ''} {contact.get('second_name') or ''}".strip()
    contact_title = contact.get('job_title') or ''

    last_email_summary = None
    if last_email_preview or last_email_body:
        last_email_summary = {
            'subject': (last_email_preview or {}).get('subject') if isinstance(last_email_preview, dict) else None,
            'preview': (last_email_preview or {}).get('preview') if isinstance(last_email_preview, dict) else None,
            'body': (last_email_body or '').strip() if last_email_body else None
        }

    news_snippets = []
    for item in news_items[:2]:
        headline = item.get('headline') or ''
        summary = item.get('summary') or ''
        news_snippets.append(f"{headline}: {summary}")

    payload = {
        'contact_name': contact_name,
        'contact_title': contact_title,
        'customer_name': customer.get('name'),
        'customer_status': customer.get('customer_status'),
        'estimated_revenue': customer.get('estimated_revenue') or 0,
        'fleet_size': customer.get('fleet_size') or 0,
        'latest_update': customer.get('latest_update'),
        'last_contact': comm_info.get('last_contact') if comm_info else None,
        'news_snippets': news_snippets,
        'last_email': last_email_summary
    }

    system_prompt = (
        "You are a warm, helpful account manager writing like a real person. "
        "We supply approved, traceable hardware that operators, OEMs, and MROs need. "
        "Draft the next email to a specific contact using the provided context. "
        "Return ONLY valid JSON with keys 'subject' and 'body'. "
        "If last_email.body is provided, treat it as the most recent email thread "
        "and draft a relevant follow-up that references prior context. "
        "Address the contact by first name when available. "
        "Write as if you already know the person: friendly, calm, and familiar, not salesy. "
        "Keep the body under 120 words, use plain language, and propose a clear next step. "
        "Avoid salesy buzzwords, hype, or exaggerated claims. "
        "Avoid stiff opener cliches like \"I hope this message finds you well\". "
        "Avoid em dashes. Do not overpraise or gush. "
        "If you mention news, keep it short and casual, avoid long place names or exact locations, "
        "and avoid using the full formal company name if it feels unnatural. "
        "Avoid email cliches like \"touch base\", \"circle back\", \"reach out\", "
        "\"see how things are going\", or \"hope you're well\". "
        "Never use filler or jargon like \"synergy\", \"synergies\", \"venture\", \"ventures\", "
        "\"leverage\", \"unlock\", \"optimize\", \"streamline\", \"world-class\", "
        "\"cutting-edge\", or \"value proposition\". "
        "Do not include placeholders like [Your Name], [Your Company], or bracketed fields. "
        "Use a simple sign-off (e.g., \"Thanks,\" or \"Best,\") with no contact details."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, default=str)}
            ],
            temperature=0.4,
            max_tokens=320
        )
        content = response.choices[0].message.content.strip()
        if content.startswith('```'):
            parts = content.split('```')
            content = parts[1] if len(parts) > 1 else content
            if content.startswith('json'):
                content = content[4:]
        suggestion = json.loads(content)
        subject = suggestion.get('subject', '').strip()
        body = _strip_ai_placeholders(suggestion.get('body', '').strip())
        return {
            'subject': subject,
            'body': body,
            'source': 'openai'
        }
    except Exception as exc:
        print(f"Contact email suggestion failed for {contact_name}: {exc}")
        fallback_subject = f"Quick follow-up with {customer.get('name')}"
        fallback_body = (
            f"Hi {contact_name or 'there'},\n\n"
            "I wanted to follow up and see if there are any upcoming needs we can support. "
            "Would you be open to a quick call this week to align?\n\n"
            "Best regards,"
        )
        return {
            'subject': fallback_subject,
            'body': fallback_body,
            'source': 'fallback'
        }


def _serialize_contact_payload(payload):
    """Make contact payloads JSON serializable."""
    if not payload:
        return None
    serialized = dict(payload)
    serialized.pop('datetime', None)
    return serialized


def _build_seed_template(template_id):
    """Return a combined subject/body seed string for AI generation."""
    if not template_id:
        return ''
    try:
        template = get_template_by_id(template_id)
    except Exception:
        template = None
    if not template:
        return ''
    subject = (template.get('subject') or '').strip()
    body = (template.get('body') or '').strip()
    if subject and body:
        return f"Subject: {subject}\n\n{body}"
    return subject or body


def _strip_ai_placeholders(text):
    """Remove bracketed placeholder lines from AI output."""
    if not text:
        return text
    cleaned_lines = []
    placeholder_pattern = re.compile(
        r'\[(your|your name|your position|your company|your contact|company|phone|email)\b',
        re.IGNORECASE
    )
    for line in text.splitlines():
        if placeholder_pattern.search(line):
            continue
        cleaned_lines.append(line)
    return '\n'.join(cleaned_lines).strip()


def _get_zero_spend_customers(salesperson_id, max_spend=0, statuses=None):
    """Fetch low-spend parent customers with light aggregates in one query.

    Args:
        salesperson_id: The salesperson to filter by
        max_spend: Maximum historical spend to include (default 0 for zero-spend only)
        statuses: Optional list of customer status strings to filter by
    """
    try:
        params = [salesperson_id]

        # Build status filter if provided
        status_filter = ""
        if statuses and isinstance(statuses, list) and len(statuses) > 0:
            placeholders = ','.join('?' for _ in statuses)
            status_filter = f"AND cs.status IN ({placeholders})"
            params.extend(statuses)

        query = f"""
            WITH child_map AS (
                SELECT main_customer_id, associated_customer_id
                FROM customer_associations
            ),
            main_customers AS (
                SELECT
                    c.id,
                    c.name,
                    c.country,
                    c.estimated_revenue,
                    c.fleet_size,
                    c.notes,
                    cs.status AS customer_status
                FROM customers c
                LEFT JOIN customer_status cs ON c.status_id = cs.id
                WHERE c.salesperson_id = ?
                  AND c.id NOT IN (SELECT associated_customer_id FROM child_map)
                  {status_filter}
            ),
            related AS (
                SELECT m.id AS main_id, m.id AS related_id
                FROM main_customers m
                UNION ALL
                SELECT cm.main_customer_id AS main_id, cm.associated_customer_id AS related_id
                FROM child_map cm
                JOIN main_customers m ON m.id = cm.main_customer_id
            ),
            spend AS (
                SELECT
                    r.main_id,
                    COALESCE(SUM(CASE
                        WHEN so.total_value IS NULL OR CAST(so.total_value AS TEXT) = '' THEN 0
                        ELSE CAST(so.total_value AS REAL)
                    END), 0) AS historical_spend
                FROM related r
                LEFT JOIN sales_orders so ON so.customer_id = r.related_id
                GROUP BY r.main_id
            ),
            latest_update AS (
                SELECT
                    r.main_id,
                    cu.update_text,
                    cu.date,
                    ROW_NUMBER() OVER (PARTITION BY r.main_id ORDER BY cu.date DESC) AS rn
                FROM related r
                JOIN customer_updates cu ON cu.customer_id = r.related_id
            )
            SELECT
                m.id,
                m.name,
                m.country,
                m.estimated_revenue,
                m.fleet_size,
                m.notes,
                m.customer_status,
                s.historical_spend,
                lu.update_text AS latest_update,
                lu.date AS latest_update_date
            FROM main_customers m
            JOIN spend s ON s.main_id = m.id
            LEFT JOIN latest_update lu ON lu.main_id = m.id AND lu.rn = 1
            WHERE s.historical_spend <= ?
        """
        params.append(max_spend)

        rows = db_execute(query, params, fetch='all') or []
        rows = [dict(row) for row in rows if row]
        main_ids = [row.get('id') for row in rows if row.get('id')]
        related_map = {main_id: [main_id] for main_id in main_ids}

        if main_ids:
            placeholders = ','.join('?' for _ in main_ids)
            assoc_rows = db_execute(
                f"""
                SELECT main_customer_id, associated_customer_id
                FROM customer_associations
                WHERE main_customer_id IN ({placeholders})
                """,
                main_ids,
                fetch='all'
            ) or []

            for assoc in (dict(row) for row in assoc_rows if row):
                main_id = assoc.get('main_customer_id')
                assoc_id = assoc.get('associated_customer_id')
                if main_id in related_map and assoc_id:
                    related_map[main_id].append(assoc_id)

        customers = []
        for row in rows:
            if not row:
                continue
            customer = dict(row)
            customer['related_customer_ids'] = related_map.get(customer.get('id'), [customer.get('id')])
            customer['has_associated_companies'] = len(customer['related_customer_ids']) > 1
            customers.append(customer)

        customers.sort(key=lambda x: float(x.get('estimated_revenue') or 0), reverse=True)
        return customers
    except Exception as exc:
        print(f"Zero-spend customer lookup failed: {exc}")
        fallback = get_salesperson_customers_with_spend(
            salesperson_id,
            sort_by='estimated_revenue',
            sort_order='desc'
        ) or []
        filtered = [
            customer for customer in fallback
            if customer and float(customer.get('historical_spend') or 0) <= 0
        ]
        return filtered


def _build_contact_suggestions(salesperson_id, graph_user_id, limit=8, offset=0, max_spend=0, statuses=None):
    """Aggregate data for the contact suggestions page."""
    customers = [customer for customer in _get_zero_spend_customers(salesperson_id, max_spend=max_spend, statuses=statuses) if customer]
    customer_groups = {
        customer['id']: customer.get('related_customer_ids') or [customer['id']]
        for customer in customers
        if customer.get('id')
    }
    contact_counts = _get_contact_counts_for_customers(customer_groups)
    customers_with_contacts = [
        customer for customer in customers
        if contact_counts.get(customer.get('id'), 0) > 0
    ]
    customers_without_contacts = [
        customer for customer in customers
        if contact_counts.get(customer.get('id'), 0) <= 0
    ]

    customers, customer_groups, contacts_by_main = _filter_customers_with_contact_emails(customers_with_contacts)

    if not customers:
        customers = []
        customer_groups = {}
        contacts_by_main = {}

    comm_snapshots = _get_customer_communication_snapshots(customer_groups, salesperson_id)
    graph_email_map = _get_latest_graph_emails_for_customers(customer_groups, graph_user_id)

    scored = []
    for customer in customers:
        customer_id = customer.get('id')
        if not customer_id:
            continue
        comm_info = comm_snapshots.get(customer_id, {})
        if not isinstance(comm_info, dict):
            comm_info = {}
        score, breakdown = _compute_suggestion_score(customer, comm_info or {})
        scored.append({
            'customer': customer,
            'comm_info': comm_info,
            'score': score,
            'breakdown': breakdown
        })

    scored.sort(key=lambda x: x['score'], reverse=True)
    total_available = len(scored)
    top_candidates = scored[offset:offset + limit]

    targets = []
    if customers_without_contacts:
        target_scored = []
        for customer in customers_without_contacts:
            if not customer.get('id'):
                continue
            score, breakdown = _compute_suggestion_score(customer, {})
            target_scored.append({
                'customer_id': customer['id'],
                'customer_name': customer.get('name'),
                'status': customer.get('customer_status'),
                'country': customer.get('country'),
                'estimated_revenue': customer.get('estimated_revenue', 0),
                'fleet_size': customer.get('fleet_size', 0),
                'mro_score': customer.get('mro_score', 0),
                'latest_update': customer.get('latest_update'),
                'score': score,
                'score_breakdown': breakdown,
                'related_customers': customer.get('related_customer_ids', []),
                'has_associated_companies': customer.get('has_associated_companies', False)
            })
        target_scored.sort(key=lambda x: x['score'], reverse=True)
        targets = target_scored[:6]

    suggestions = []
    for entry in top_candidates:
        customer = (entry or {}).get('customer') or {}
        comm_info = (entry or {}).get('comm_info')
        if not isinstance(comm_info, dict):
            comm_info = {}
        if not customer.get('id'):
            continue
        last_email_preview = None
        last_email = comm_info.get('last_email') or {}
        if not isinstance(last_email, dict):
            last_email = {}
        if last_email.get('message_id'):
            last_email_preview = _get_email_preview_from_cache(
                last_email.get('message_id'),
                graph_user_id
            )

        last_contact_serialized = _serialize_contact_payload(comm_info.get('last_contact'))
        last_email_serialized = _serialize_contact_payload(comm_info.get('last_email'))
        if last_email_serialized and last_email_preview:
            last_email_serialized.update(last_email_preview)

        suggestions.append({
            'customer_id': customer['id'],
            'customer_name': customer.get('name'),
            'status': customer.get('customer_status'),
            'country': customer.get('country'),
            'historical_spend': customer.get('historical_spend', 0),
            'estimated_revenue': customer.get('estimated_revenue', 0),
            'fleet_size': customer.get('fleet_size', 0),
            'mro_score': customer.get('mro_score', 0),
            'latest_update': customer.get('latest_update'),
            'most_recent_order': customer.get('most_recent_order_date'),
            'customer_notes': customer.get('notes') or '',
            'score': entry['score'],
            'score_breakdown': entry['breakdown'],
            'last_contact': last_contact_serialized,
            'last_email': last_email_serialized,
            'last_graph_email': graph_email_map.get(customer['id']),
            'contacts': contacts_by_main.get(customer['id'], []),
            'news_items': [],
            'suggested_email': None,
            'related_customers': customer.get('related_customer_ids', []),
            'has_associated_companies': customer.get('has_associated_companies', False),
            'priority_name': customer.get('priority_name')
        })

    return suggestions, False, total_available, targets

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

        quotes_by_day = []
        this_month_quotes_value_gbp = 0.0
        try:
            quote_rows = db_execute(
                """
                SELECT
                    DATE(cql.quoted_on) AS quoted_date,
                    COUNT(DISTINCT pl.id) AS quoted_lists,
                    COALESCE(SUM(
                        COALESCE(cql.quote_price_gbp, 0) *
                        COALESCE(NULLIF(pll.chosen_qty, 0), pll.quantity, 0)
                    ), 0) AS quoted_value_gbp
                FROM customer_quote_lines cql
                JOIN parts_list_lines pll ON pll.id = cql.parts_list_line_id
                JOIN parts_lists pl ON pl.id = pll.parts_list_id
                WHERE pl.salesperson_id = ?
                  AND cql.quoted_status = 'quoted'
                  AND cql.quoted_on IS NOT NULL
                GROUP BY DATE(cql.quoted_on)
                ORDER BY quoted_date DESC
                LIMIT 10
                """,
                (salesperson_id,),
                fetch='all'
            ) or []

            def _format_quote_date(value):
                if isinstance(value, datetime):
                    return value.strftime('%Y-%m-%d')
                if isinstance(value, date):
                    return value.strftime('%Y-%m-%d')
                if value is None:
                    return ''
                return str(value)

            quotes_by_day = [
                {
                    'quoted_date': _format_quote_date(row.get('quoted_date')),
                    'quoted_lists': int(row.get('quoted_lists') or 0),
                    'quoted_value_gbp': float(row.get('quoted_value_gbp') or 0),
                }
                for row in [dict(r) for r in quote_rows]
            ]

            month_quote_row = db_execute(
                """
                SELECT
                    COALESCE(SUM(
                        COALESCE(cql.quote_price_gbp, 0) *
                        COALESCE(NULLIF(pll.chosen_qty, 0), pll.quantity, 0)
                    ), 0) AS quoted_value_gbp
                FROM customer_quote_lines cql
                JOIN parts_list_lines pll ON pll.id = cql.parts_list_line_id
                JOIN parts_lists pl ON pl.id = pll.parts_list_id
                WHERE pl.salesperson_id = ?
                  AND cql.quoted_status = 'quoted'
                  AND cql.quoted_on IS NOT NULL
                  AND cql.quoted_on::date BETWEEN date_trunc('month', CURRENT_DATE)::date AND CURRENT_DATE
                """,
                (salesperson_id,),
                fetch='one'
            )
            if month_quote_row:
                this_month_quotes_value_gbp = float(month_quote_row['quoted_value_gbp'] or 0)
        except Exception as e:
            print(f"DEBUG: Error loading quotes by day: {e}")
            quotes_by_day = []
            this_month_quotes_value_gbp = 0.0

        pinned_parts_lists_count = 0
        pinned_parts_lists_value_gbp = 0.0
        try:
            pinned_summary_row = db_execute(
                """
                SELECT
                    COUNT(DISTINCT pl.id) AS pinned_count,
                    COALESCE(SUM(
                        CASE
                            WHEN cql.quoted_status = 'quoted'
                                 AND COALESCE(cql.is_no_bid::int, 0) = 0
                                 AND cql.quote_price_gbp > 0
                            THEN cql.quote_price_gbp * COALESCE(NULLIF(pll.chosen_qty, 0), pll.quantity, 0)
                            ELSE 0
                        END
                    ), 0) AS pinned_value_gbp
                FROM parts_lists pl
                LEFT JOIN parts_list_lines pll ON pll.parts_list_id = pl.id
                LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                WHERE COALESCE(pl.is_pinned, FALSE) = TRUE
                  AND pl.salesperson_id = ?
                """,
                (salesperson_id,),
                fetch='one'
            )
            if pinned_summary_row:
                pinned_parts_lists_count = int(pinned_summary_row['pinned_count'] or 0)
                pinned_parts_lists_value_gbp = float(pinned_summary_row['pinned_value_gbp'] or 0)
        except Exception as e:
            print(f"DEBUG: Error loading pinned parts lists summary: {e}")
            pinned_parts_lists_count = 0
            pinned_parts_lists_value_gbp = 0.0

        quoted_status_id = request.args.get('quoted_status_id', default=None, type=int)
        parts_date_range = request.args.get('parts_date_range', default='14days', type=str)
        parts_custom_date = request.args.get('parts_custom_date', default=None, type=str)
        if parts_date_range not in ('14days', 'all', 'custom'):
            parts_date_range = '14days'

        parts_list_statuses = []
        try:
            status_rows = db_execute(
                """
                SELECT id, name
                FROM parts_list_statuses
                ORDER BY display_order ASC, name ASC
                """,
                fetch='all'
            ) or []
            parts_list_statuses = [dict(r) for r in status_rows]
        except Exception as e:
            print(f"DEBUG: Error loading parts list statuses: {e}")
            parts_list_statuses = []

        # Default to "Quoted" status if no status is specified
        if quoted_status_id is None and parts_list_statuses:
            for status in parts_list_statuses:
                if status['name'].lower() == 'quoted':
                    quoted_status_id = status['id']
                    break

        top_quoted_lists = []
        try:
            date_clause = ""
            status_clause = ""
            params = [salesperson_id]
            if quoted_status_id:
                status_clause = "AND pl.status_id = ?"
                params.append(quoted_status_id)

            if parts_date_range == '14days':
                date_clause = "AND cql.quoted_on >= CURRENT_DATE - interval '14 days' AND cql.quoted_on <= CURRENT_DATE"
            elif parts_date_range == 'custom' and parts_custom_date:
                date_clause = "AND cql.quoted_on::date = ?::date"
                params.append(parts_custom_date)
            # 'all' means no date filter

            top_rows = db_execute(
                f"""
                SELECT
                    pl.id,
                    pl.name,
                    pl.notes,
                    c.name AS customer_name,
                    pl.status_id,
                    pls.name AS status_name,
                    pl.date_modified,
                    COALESCE(SUM(CASE
                        WHEN cql.quoted_status = 'quoted'
                             AND COALESCE(cql.is_no_bid, 0) = 0
                             AND cql.quote_price_gbp > 0
                        THEN cql.quote_price_gbp * COALESCE(NULLIF(pll.chosen_qty, 0), pll.quantity, 0)
                        ELSE 0 END), 0) AS quoted_value_gbp
                FROM parts_lists pl
                LEFT JOIN parts_list_statuses pls ON pls.id = pl.status_id
                LEFT JOIN customers c ON c.id = pl.customer_id
                LEFT JOIN parts_list_lines pll ON pll.parts_list_id = pl.id
                LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                WHERE pl.salesperson_id = ?
                {status_clause}
                {date_clause}
                GROUP BY pl.id, pl.name, pl.notes, c.name, pl.status_id, pl.date_modified, pls.name
                HAVING COALESCE(SUM(CASE
                    WHEN cql.quoted_status = 'quoted'
                         AND COALESCE(cql.is_no_bid, 0) = 0
                         AND cql.quote_price_gbp > 0
                    THEN cql.quote_price_gbp * COALESCE(NULLIF(pll.chosen_qty, 0), pll.quantity, 0)
                    ELSE 0 END), 0) > 0
                ORDER BY quoted_value_gbp DESC, pl.date_modified DESC
                LIMIT 15
                """,
                params,
                fetch='all'
            ) or []

            def _format_list_date(value):
                if isinstance(value, datetime):
                    return value.strftime('%Y-%m-%d')
                if isinstance(value, date):
                    return value.strftime('%Y-%m-%d')
                if value is None:
                    return ''
                return str(value)

            top_quoted_lists = [
                {
                    'id': row.get('id'),
                    'name': row.get('name') or '',
                    'notes': row.get('notes') or '',
                    'customer_name': row.get('customer_name') or '',
                    'status_id': row.get('status_id'),
                    'status_name': row.get('status_name') or '',
                    'date_modified': _format_list_date(row.get('date_modified')),
                    'quoted_value_gbp': float(row.get('quoted_value_gbp') or 0),
                }
                for row in [dict(r) for r in top_rows]
            ]
        except Exception as e:
            print(f"DEBUG: Error loading top quoted lists: {e}")
            top_quoted_lists = []

        tracker_step_customers = []
        try:
            tracker_rows = db_execute(
                """
                SELECT
                    e.id AS entry_id,
                    c.id AS customer_id,
                    c.name AS customer_name,
                    ns.id AS step_id,
                    ns.description AS step_description,
                    ns.is_completed,
                    ns.completed_at,
                    ns.position,
                    ns.created_at
                FROM team_tracker_entries e
                JOIN customers c ON c.id = e.customer_id
                LEFT JOIN team_tracker_next_steps ns ON ns.entry_id = e.id
                WHERE e.salesperson_id = ?
                  AND e.is_active = TRUE
                ORDER BY c.name ASC, ns.is_completed ASC, ns.position ASC, ns.created_at ASC
                """,
                (salesperson_id,),
                fetch='all'
            ) or []

            grouped_steps = {}
            for row in [dict(r) for r in tracker_rows]:
                customer_id = row.get('customer_id')
                if customer_id not in grouped_steps:
                    grouped_steps[customer_id] = {
                        'entry_id': row.get('entry_id'),
                        'id': customer_id,
                        'name': row.get('customer_name') or '',
                        'open_steps': [],
                        'completed_steps': []
                    }

                step_id = row.get('step_id')
                if not step_id:
                    continue

                step_data = {
                    'id': step_id,
                    'description': row.get('step_description') or '',
                    'completed_at': row.get('completed_at')
                }
                if row.get('is_completed'):
                    grouped_steps[customer_id]['completed_steps'].append(step_data)
                else:
                    grouped_steps[customer_id]['open_steps'].append(step_data)

            tracker_step_customers = []
            for customer in grouped_steps.values():
                if not customer['open_steps'] and not customer['completed_steps']:
                    continue

                sorted_completed = sorted(
                    customer['completed_steps'],
                    key=lambda s: (
                        s.get('completed_at').isoformat()
                        if hasattr(s.get('completed_at'), 'isoformat')
                        else str(s.get('completed_at') or '')
                    ),
                    reverse=True
                )
                customer['completed_preview'] = sorted_completed[:2]
                customer['open_count'] = len(customer['open_steps'])
                customer['completed_count'] = len(customer['completed_steps'])
                tracker_step_customers.append(customer)

            tracker_step_customers.sort(key=lambda c: (c['name'] or '').lower())

        except Exception as e:
            print(f"DEBUG: Error loading team tracker steps: {e}")

        t_render = time.perf_counter()
        response = render_template(template,
            salesperson=salesperson,
            all_salespeople=all_salespeople,
            assigned_customers=assigned_customers,
            pending_orders=salesperson_orders,
            call_list_prefill=call_list_prefill,
            quotes_by_day=quotes_by_day,
            top_quoted_lists=top_quoted_lists,
            parts_list_statuses=parts_list_statuses,
            quoted_status_id=quoted_status_id,
            parts_date_range=parts_date_range,
            parts_custom_date=parts_custom_date,
            tracker_step_customers=tracker_step_customers,
            this_month_quotes_value_gbp=this_month_quotes_value_gbp,
            pinned_parts_lists_count=pinned_parts_lists_count,
            pinned_parts_lists_value_gbp=pinned_parts_lists_value_gbp
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

@salespeople_bp.route('/<int:salesperson_id>/contact-suggestions')
@login_required
def contact_suggestions_page(salesperson_id):
    """Render the next-contact suggestions page for low-spend customers."""
    salesperson = get_salesperson_by_id(salesperson_id)
    if not salesperson:
        flash('Salesperson not found!', 'error')
        return redirect(url_for('salespeople.dashboard'))

    breadcrumbs = generate_breadcrumbs(
        ('Home', url_for('index')),
        ('Salespeople', url_for('salespeople.dashboard')),
        (salesperson['name'], url_for('salespeople.activity', salesperson_id=salesperson_id)),
        ('Customers', url_for('salespeople.customers', salesperson_id=salesperson_id)),
        ('Next Contact Suggestions', url_for('salespeople.contact_suggestions_page', salesperson_id=salesperson_id))
    )

    try:
        customer_statuses = get_customer_status_options() or []
        # Convert Row objects to dicts if needed
        if customer_statuses and hasattr(customer_statuses[0], 'keys'):
            customer_statuses = [dict(row) for row in customer_statuses]
    except Exception as e:
        print(f"Error loading customer statuses: {e}")
        import traceback
        traceback.print_exc()
        customer_statuses = []

    return render_template(
        'salespeople/contact_suggestions.html',
        salesperson=salesperson,
        breadcrumbs=breadcrumbs,
        customer_statuses=customer_statuses
    )


@salespeople_bp.route('/<int:salesperson_id>/contact-suggestions/data')
@login_required
def contact_suggestions_data(salesperson_id):
    """API endpoint that assembles low-spend customer outreach suggestions."""
    salesperson = get_salesperson_by_id(salesperson_id)
    if not salesperson:
        return jsonify({'success': False, 'error': 'Salesperson not found'}), 404

    limit = request.args.get('limit', 8, type=int) or 8
    offset = request.args.get('offset', 0, type=int) or 0
    max_spend = request.args.get('max_spend', 0, type=float) or 0
    statuses_param = request.args.get('statuses', '')
    statuses = [s.strip() for s in statuses_param.split(',') if s.strip()] if statuses_param else None
    try:
        graph_user_id = getattr(current_user, 'id', None)
        suggestions, cached_news_used, total_available, no_contact_targets = _build_contact_suggestions(
            salesperson_id,
            graph_user_id,
            limit=limit,
            offset=offset,
            max_spend=max_spend,
            statuses=statuses
        )
        return jsonify({
            'success': True,
            'suggestions': suggestions,
            'targets_without_contacts': no_contact_targets,
            'total_available': total_available,
            'next_offset': offset + len(suggestions),
            'generated_at': datetime.utcnow().isoformat() + 'Z',
            'cached_news_used': cached_news_used
        })
    except Exception as exc:
        print(f"Error generating contact suggestions: {exc}")
        import traceback
        print(traceback.format_exc())
        return jsonify({'success': False, 'error': str(exc)}), 500


@salespeople_bp.route('/<int:salesperson_id>/contact-suggestions/ai', methods=['POST'])
@login_required
def contact_suggestions_ai(salesperson_id):
    """Generate AI-backed email + news for a single customer suggestion."""
    salesperson = get_salesperson_by_id(salesperson_id)
    if not salesperson:
        return jsonify({'success': False, 'error': 'Salesperson not found'}), 404

    payload = request.get_json(silent=True) or {}
    customer_id = payload.get('customer_id')
    if not customer_id:
        return jsonify({'success': False, 'error': 'Customer ID is required'}), 400
    try:
        customer_id = int(customer_id)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Customer ID is invalid'}), 400

    template_id = payload.get('template_id')
    try:
        template_id = int(template_id) if template_id else None
    except (TypeError, ValueError):
        template_id = None
    seed_template = _build_seed_template(template_id)
    graph_email_subject = payload.get('graph_email_subject')
    graph_email_body = payload.get('graph_email_body')
    include_news = payload.get('include_news')
    if include_news is None:
        include_news = True
    elif isinstance(include_news, str):
        include_news = include_news.strip().lower() in ('1', 'true', 'yes', 'on')
    else:
        include_news = bool(include_news)

    customers = _get_zero_spend_customers(salesperson_id)
    customer = next((c for c in customers if c and c.get('id') == customer_id), None)
    if not customer:
        return jsonify({'success': False, 'error': 'Customer not found'}), 404

    customer_groups = {
        customer['id']: customer.get('related_customer_ids') or [customer['id']]
    }
    comm_snapshots = _get_customer_communication_snapshots(customer_groups, salesperson_id)
    comm_info = comm_snapshots.get(customer['id'], {}) or {}
    if not isinstance(comm_info, dict):
        comm_info = {}

    last_email_preview = None
    last_email = comm_info.get('last_email') or {}
    if not isinstance(last_email, dict):
        last_email = {}
    if last_email.get('message_id'):
        graph_user_id = getattr(current_user, 'id', None)
        last_email_preview = _get_email_preview_from_cache(
            last_email.get('message_id'),
            graph_user_id
        )
    graph_email_body_clean = None
    if graph_email_body or graph_email_subject:
        preview_source = graph_email_body or ''
        graph_email_body_clean = _strip_html(preview_source).strip()
        last_email_preview = {
            'subject': (graph_email_subject or '').strip(),
            'preview': graph_email_body_clean[:1200]
        }

    if include_news:
        news_map, cached_news_used = _build_news_lookup_for_customers(salesperson_id)
        _hydrate_news_for_customers([customer], news_map)
        news_items = news_map.get((customer.get('name') or '').lower(), [])
    else:
        news_items = []
        cached_news_used = False

    suggested_email = _generate_email_suggestion(
        customer,
        comm_info,
        news_items,
        last_email_preview,
        seed_template=seed_template,
        last_email_body=graph_email_body_clean
    )

    return jsonify({
        'success': True,
        'customer_id': customer['id'],
        'suggested_email': suggested_email,
        'news_items': news_items,
        'cached_news_used': cached_news_used
    })
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

        customer_orders = get_customer_orders_consolidated(related_customer_ids)
        print(f"DEBUG: Retrieved {len(customer_orders) if customer_orders else 0} consolidated orders")

        # Get customer tags (only from main customer)
        customer_tags = get_tags_by_customer_id(customer_id)
        print(f"DEBUG: Retrieved {len(customer_tags)} tags for customer {customer_id}")

        # Get company types for the customer
        company_types = get_company_types_by_customer_id(customer_id, include_ids=True)
        print(f"DEBUG: Retrieved {len(company_types)} company types for customer {customer_id}")

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # For AJAX requests, render a partial template
            html = render_template(
                'salespeople/customer_details.html',
                customer=customer,
                updates=customer_updates,
                orders=customer_orders,
                tags=customer_tags,
                contacts=contacts,
                company_types=company_types
            )
            return html

        # Fallback to full page template
        return render_template(
            'salespeople/customer_detail_page.html',
            customer=customer,
            updates=customer_updates,
            orders=customer_orders,
            tags=customer_tags,
            contacts=contacts,
            company_types=company_types
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
            active_orders = get_customer_active_orders_count_consolidated(related_customer_ids)
        except Exception as e:
            print(f"DEBUG customer_data: Error getting active counts: {str(e)}")
            active_orders = 0

        # Get customer tags for the response (only from main customer)
        customer_tags = get_tags_by_customer_id(customer_id)

        # Chart data
        if time_period == 'yearly':
            # For yearly data, use the existing approach - data is already aggregated by year
            years = []
            order_values = []

            # Extract data from orders which are already grouped by year
            for order in orders:
                year = order['date_entered'].split('-')[0]
                years.append(year)
                order_values.append(float(order['total_value'] or 0))

            chart_data = {
                'labels': years,
                'orders': order_values
            }

        elif time_period == 'last_12_months':
            # Last 12 months - group by month
            month_data = defaultdict(lambda: {'orders': 0})

            # Generate all 12 month labels
            labels = []
            for i in range(12):
                month_date = today.replace(day=1) - timedelta(days=i * 30)  # Approximate
                month_label = month_date.strftime('%b %Y')
                labels.insert(0, month_label)
                month_data[month_label] = {'orders': 0}

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
            orders_series = [month_data[label]['orders'] for label in labels]

            chart_data = {
                'labels': labels,
                'orders': orders_series
            }

        else:  # last_30_days
            # Last 30 days - group by day
            day_data = defaultdict(lambda: {'orders': 0})

            # Generate all 30 day labels
            labels = []
            for i in range(30):
                day_date = today - timedelta(days=i)
                day_label = day_date.strftime('%d %b')
                labels.insert(0, day_label)
                day_data[day_label] = {'orders': 0}

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
            orders_series = [day_data[label]['orders'] for label in labels]

            chart_data = {
                'labels': labels,
                'orders': orders_series
            }

        return jsonify({
            'orders': orders,
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
            month_keys = []
            month_dict = {}  # For mapping month strings to positions

            for i in range(24):  # Changed from 12 to 24
                # Use calendar month arithmetic to avoid duplicate/skip month labels.
                month_date = _month_start(today, i)
                month_label = month_date.strftime('%b %Y')  # e.g. "Jan 2025"
                month_key = month_date.strftime('%Y-%m')  # e.g. "2025-01"

                # Add to the start of the lists (to get chronological order)
                chart_labels.insert(0, month_label)
                month_keys.insert(0, month_key)
                month_dict[month_key] = 23 - i  # Map SQL month format to array position (changed from 11-i)

            db = get_db_connection()
            goal_month_key = today.strftime('%Y-%m')
            goal_rows = db.execute(
                """
                SELECT target_month, goal_amount
                FROM salesperson_monthly_goals
                WHERE salesperson_id = ? AND target_month BETWEEN ? AND ?
                """,
                (salesperson_id, month_keys[0], month_keys[-1])
            ).fetchall()
            goal_series = [None] * len(month_keys)
            goal_map = {}
            for row in goal_rows or []:
                try:
                    amount = float(row['goal_amount'] or 0)
                except (ValueError, TypeError):
                    amount = 0
                goal_map[row['target_month']] = amount
                if amount > 0 and row['target_month'] in month_dict:
                    goal_series[month_dict[row['target_month']]] = amount

            goal_month_index = month_dict.get(goal_month_key)
            goal_amount = goal_map.get(goal_month_key, 0) if goal_month_index is not None else 0
            goal_month_label = chart_labels[goal_month_index] if goal_month_index is not None else None
            result['monthly_goal'] = {
                'amount': goal_amount,
                'month_index': goal_month_index,
                'month_label': goal_month_label
            }
            result['monthly_goal_series'] = goal_series

            # 4. Personal sales data with customer breakdown
            start_date = f"{month_keys[0]}-01"

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


@salespeople_bp.route('/contact/<int:contact_id>')
@login_required
def contact_detail_page(contact_id):
    """Full page view for a specific contact"""
    try:
        salesperson_id = request.args.get('salesperson_id', type=int)

        with db_cursor() as db:
            # Get contact with all related info
            contact = _execute_with_cursor(db, '''
                SELECT c.*,
                       cu.name as customer_name,
                       cu.website as customer_website,
                       cu.country as customer_country,
                       cs.name as status_name,
                       cs.color as status_color,
                       sp.name as salesperson_name
                FROM contacts c
                LEFT JOIN customers cu ON c.customer_id = cu.id
                LEFT JOIN contact_statuses cs ON c.status_id = cs.id
                LEFT JOIN salespeople sp ON cu.salesperson_id = sp.id
                WHERE c.id = ?
            ''', (contact_id,)).fetchone()

            if not contact:
                flash('Contact not found', 'error')
                return redirect(url_for('salespeople.index'))

            contact = dict(contact)

            # Get communications for this contact
            communications = get_contact_communications(contact_id, salesperson_id)

            # Get communication stats
            comm_stats = _execute_with_cursor(db, '''
                SELECT
                    COUNT(*) as total_communications,
                    MAX(date) as last_communication_date,
                    COUNT(CASE WHEN communication_type = 'Email' THEN 1 END) as email_count,
                    COUNT(CASE WHEN communication_type = 'Phone' THEN 1 END) as phone_count,
                    COUNT(CASE WHEN communication_type = 'Meeting' THEN 1 END) as meeting_count
                FROM contact_communications
                WHERE contact_id = ?
            ''', (contact_id,)).fetchone()

            comm_stats = dict(comm_stats) if comm_stats else {
                'total_communications': 0,
                'last_communication_date': None,
                'email_count': 0,
                'phone_count': 0,
                'meeting_count': 0
            }

            # Get all contact statuses for edit form
            contact_statuses = get_all_contact_statuses()

            return render_template(
                'salespeople/contact_detail_page.html',
                contact=contact,
                communications=communications,
                comm_stats=comm_stats,
                contact_statuses=contact_statuses,
                salesperson_id=salesperson_id,
                communication_types=["Email", "Phone", "Meeting", "Video Call", "Other"]
            )

    except Exception as e:
        print(f"Error loading contact detail page: {str(e)}")
        import traceback
        print(traceback.format_exc())
        flash('Error loading contact details', 'error')
        if salesperson_id:
            return redirect(url_for('salespeople.contacts', salesperson_id=salesperson_id))
        return redirect(url_for('salespeople.dashboard'))


@salespeople_bp.route('/contact_details/<int:contact_id>/news')
@login_required
def contact_details_news(contact_id):
    """Return latest customer news hints for the contact details view."""
    salesperson_id = request.args.get('salesperson_id', type=int)
    contact = db_execute(
        "SELECT id, customer_id FROM contacts WHERE id = ?",
        (contact_id,),
        fetch='one'
    )
    if not contact:
        return jsonify({'success': False, 'error': 'Contact not found'}), 404
    contact = dict(contact)

    customer = _get_customer_outreach_profile(contact.get('customer_id'))
    if not customer:
        return jsonify({'success': False, 'error': 'Customer not found'}), 404

    if salesperson_id and customer.get('salesperson_id') != salesperson_id:
        return jsonify({'success': False, 'error': 'Contact not authorized'}), 403

    news_map, cached_news_used = _build_news_lookup_for_customers(customer.get('salesperson_id'))
    _hydrate_news_for_customers([customer], news_map, max_fetch=1)
    news_items = news_map.get((customer.get('name') or '').lower(), [])

    return jsonify({
        'success': True,
        'contact_id': contact_id,
        'customer_id': customer.get('id'),
        'news_items': news_items,
        'cached_news_used': cached_news_used
    })


@salespeople_bp.route('/contact_details/<int:contact_id>/next-email', methods=['POST'])
@login_required
def contact_details_next_email(contact_id):
    """Generate the next email suggestion for a contact."""
    payload = request.get_json(silent=True) or {}
    salesperson_id = payload.get('salesperson_id')
    try:
        salesperson_id = int(salesperson_id) if salesperson_id else None
    except (TypeError, ValueError):
        salesperson_id = None

    contact = db_execute(
        """
        SELECT id, customer_id, name, second_name, job_title, email
        FROM contacts
        WHERE id = ?
        """,
        (contact_id,),
        fetch='one'
    )
    if not contact:
        return jsonify({'success': False, 'error': 'Contact not found'}), 404
    contact = dict(contact)

    customer = _get_customer_outreach_profile(contact.get('customer_id'))
    if not customer:
        return jsonify({'success': False, 'error': 'Customer not found'}), 404

    if salesperson_id and customer.get('salesperson_id') != salesperson_id:
        return jsonify({'success': False, 'error': 'Contact not authorized'}), 403

    communications = get_contact_communications(contact_id, salesperson_id)
    comm_info = _build_contact_comm_snapshot(communications)

    graph_email_subject = payload.get('graph_email_subject')
    graph_email_body = payload.get('graph_email_body')
    include_news = payload.get('include_news')
    if include_news is None:
        include_news = True
    elif isinstance(include_news, str):
        include_news = include_news.strip().lower() in ('1', 'true', 'yes', 'on')
    else:
        include_news = bool(include_news)

    last_email_preview = None
    graph_email_body_clean = None
    if graph_email_body or graph_email_subject:
        preview_source = graph_email_body or ''
        graph_email_body_clean = _strip_html(preview_source).strip()
        last_email_preview = {
            'subject': (graph_email_subject or '').strip(),
            'preview': graph_email_body_clean[:1200]
        }

    if not last_email_preview:
        last_email = comm_info.get('last_email') or {}
        if last_email.get('message_id'):
            graph_user_id = getattr(current_user, 'id', None)
            last_email_preview = _get_email_preview_from_cache(
                last_email.get('message_id'),
                graph_user_id
            )

    if include_news:
        news_map, cached_news_used = _build_news_lookup_for_customers(customer.get('salesperson_id'))
        _hydrate_news_for_customers([customer], news_map, max_fetch=1)
        news_items = news_map.get((customer.get('name') or '').lower(), [])
    else:
        news_items = []
        cached_news_used = False

    suggested_email = _generate_contact_email_suggestion(
        contact,
        customer,
        comm_info,
        news_items,
        last_email_preview,
        last_email_body=graph_email_body_clean
    )

    return jsonify({
        'success': True,
        'contact_id': contact_id,
        'customer_id': customer.get('id'),
        'suggested_email': suggested_email,
        'news_items': news_items,
        'cached_news_used': cached_news_used
    })


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
        call_list_snoozed_contact_ids = set()
        if contacts and call_list_contact_ids and _call_list_has_snoozed_until():
            contact_ids = [contact['id'] for contact in contacts]
            if contact_ids:
                placeholders = ','.join('?' for _ in contact_ids)
                call_list_rows = db_execute(
                    f"""
                    SELECT contact_id,
                           CASE
                               WHEN snoozed_until IS NOT NULL
                                    AND snoozed_until > CURRENT_TIMESTAMP
                               THEN TRUE
                               ELSE FALSE
                           END AS is_snoozed
                    FROM call_list
                    WHERE salesperson_id = ?
                      AND is_active = TRUE
                      AND contact_id IN ({placeholders})
                    """,
                    [salesperson_id] + contact_ids,
                    fetch='all'
                ) or []

                for row in call_list_rows:
                    if row.get('is_snoozed'):
                        call_list_snoozed_contact_ids.add(row['contact_id'])

        # Mark which contacts are on the call list
        for contact in contacts:
            contact['is_on_call_list'] = contact['id'] in call_list_contact_ids
            contact['is_snoozed'] = contact['id'] in call_list_snoozed_contact_ids

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
        target_month_start = _month_start(today, months_back)

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
        month_keys = []
        month_dict = {}

        for i in range(24):
            month_date = _month_start(today, i)
            month_label = month_date.strftime('%b %Y')
            month_key = month_date.strftime('%Y-%m')

            chart_labels.insert(0, month_label)
            month_keys.insert(0, month_key)
            month_dict[month_key] = 23 - i

        # Get sales data for ALL associated customers
        start_date = f"{month_keys[0]}-01"
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
            'total_news_items': 0,
            'filtered_duplicates': 0
        }

    top_customers = get_watched_customers_for_news(salesperson_id, limit=25)
    if not top_customers:
        result = {
            'news_items': [],
            'last_updated': datetime.now().isoformat(),
            'total_customers_checked': 0,
            'successful_customers': 0,
            'total_news_items': 0,
            'filtered_duplicates': 0
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

    # Sort by relevance and date
    all_news_items.sort(
        key=lambda x: (x.get('relevance_score', 0), x.get('published_date', '')),
        reverse=True
    )
    
    # Filter out duplicates (news that has already been sent)
    original_count = len(all_news_items)
    filtered_news_items = filter_duplicate_news(salesperson_id, all_news_items)
    filtered_duplicates = original_count - len(filtered_news_items)
    
    # Take top 20 after filtering
    final_news_items = filtered_news_items[:20]

    result = {
        'news_items': final_news_items,
        'last_updated': datetime.now().isoformat(),
        'total_customers_checked': len(top_customers),
        'successful_customers': successful_customers,
        'total_news_items': len(final_news_items),
        'filtered_duplicates': filtered_duplicates
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
        
        # Filter out duplicates (news that has already been sent)
        original_count = len(all_news_items)
        filtered_news_items = filter_duplicate_news(salesperson_id, all_news_items)
        filtered_duplicates = original_count - len(filtered_news_items)
        
        # Take top 20 after filtering
        final_news_items = filtered_news_items[:20]

        # Cache the results
        result = {
            'news_items': final_news_items,
            'last_updated': datetime.now().isoformat(),
            'total_customers_checked': len(top_customers),
            'successful_customers': successful_customers,
            'total_news_items': len(final_news_items),
            'filtered_duplicates': filtered_duplicates
        }

        cache_key = get_cache_key(salesperson_id)
        cache_news(cache_key, result)

        # Send completion email
        email_sent = send_news_email(salesperson_id, salesperson.get('name') if salesperson else None, result)
        
        # Store sent news items to prevent future duplicates (only if email was sent)
        if email_sent and final_news_items:
            store_sent_news_items(salesperson_id, final_news_items)
        
        yield f"data: {json.dumps({'status': 'completed', 'filtered_duplicates': filtered_duplicates, **result})}\n\n"

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


@salespeople_bp.route('/<int:salesperson_id>/snooze-call-list', methods=['POST'])
@login_required
def snooze_call_list_route(salesperson_id):
    """Snooze a call list item until a specific future date."""
    try:
        data = request.get_json()
        call_list_id = data.get('call_list_id')
        snooze_days = data.get('snooze_days')

        if not call_list_id or not snooze_days:
            return jsonify({'success': False, 'error': 'Missing call list ID or snooze days'}), 400

        try:
            snooze_days = int(snooze_days)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Invalid snooze days'}), 400

        if snooze_days <= 0:
            return jsonify({'success': False, 'error': 'Snooze days must be positive'}), 400

        snooze_until = datetime.now() + timedelta(days=snooze_days)
        result = snooze_call_list_entry(call_list_id, salesperson_id, snooze_until)
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
        saved_targets_query = """
            SELECT customer_id, target_amount, notes, comments, response, is_locked
            FROM customer_monthly_targets
            WHERE salesperson_id = ? AND target_month = ?
        """
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
        recent_orders_30d_map = {}
        recent_quotes_30d_map = {}
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

            recent_orders_query = f"""
                SELECT
                    customer_id,
                    COUNT(*) AS recent_order_count,
                    COALESCE(SUM(
                        CASE
                            WHEN total_value IS NULL OR total_value::text = '' THEN 0
                            ELSE CAST(total_value AS REAL)
                        END
                    ), 0) AS recent_order_value
                FROM sales_orders
                WHERE customer_id IN ({placeholders})
                  AND date_entered >= CURRENT_DATE - INTERVAL '30 days'
                  AND date_entered <= CURRENT_DATE
                GROUP BY customer_id
            """
            recent_order_rows = db_execute(recent_orders_query, list(relevant_customer_ids), fetch='all') or []
            recent_orders_30d_map = {
                row['customer_id']: {
                    'value': float(row['recent_order_value'] or 0),
                    'count': int(row['recent_order_count'] or 0)
                }
                for row in recent_order_rows
            }

            recent_quotes_query = f"""
                SELECT
                    pl.customer_id,
                    COUNT(DISTINCT cql.id) AS recent_quote_count,
                    COALESCE(SUM(
                        CASE
                            WHEN cql.quoted_status = 'quoted'
                                 AND COALESCE(cql.is_no_bid, 0) = 0
                                 AND COALESCE(cql.quote_price_gbp, 0) > 0
                            THEN COALESCE(cql.quote_price_gbp, 0) * COALESCE(NULLIF(pll.chosen_qty, 0), pll.quantity, 0)
                            ELSE 0
                        END
                    ), 0) AS recent_quote_value
                FROM parts_lists pl
                JOIN parts_list_lines pll ON pll.parts_list_id = pl.id
                JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                WHERE pl.customer_id IN ({placeholders})
                  AND cql.quoted_on IS NOT NULL
                  AND cql.quoted_on::date >= CURRENT_DATE - INTERVAL '30 days'
                  AND cql.quoted_on::date <= CURRENT_DATE
                GROUP BY pl.customer_id
            """
            recent_quote_rows = db_execute(recent_quotes_query, list(relevant_customer_ids), fetch='all') or []
            recent_quotes_30d_map = {
                row['customer_id']: {
                    'value': float(row['recent_quote_value'] or 0),
                    'count': int(row['recent_quote_count'] or 0)
                }
                for row in recent_quote_rows
            }
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

            recent_orders_30d = sum((recent_orders_30d_map.get(sub_id) or {}).get('value', 0) for sub_id in all_ids)
            recent_orders_count_30d = sum((recent_orders_30d_map.get(sub_id) or {}).get('count', 0) for sub_id in all_ids)
            recent_quotes_30d = sum((recent_quotes_30d_map.get(sub_id) or {}).get('value', 0) for sub_id in all_ids)
            recent_quotes_count_30d = sum((recent_quotes_30d_map.get(sub_id) or {}).get('count', 0) for sub_id in all_ids)
            conversion_pct_30d = None
            if recent_quotes_30d > 0:
                conversion_pct_30d = round((recent_orders_30d / recent_quotes_30d) * 100, 1)

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
                'next_action': saved_data.get('notes') or '',
                'comments': saved_data.get('comments') or '',
                'response': saved_data.get('response') or '',
                'calc_method': calc_method,
                'is_locked': is_locked,
                'associated_count': len(all_ids),
                'recent_orders_30d': recent_orders_30d,
                'recent_orders_count_30d': recent_orders_count_30d,
                'recent_quotes_30d': recent_quotes_30d,
                'recent_quotes_count_30d': recent_quotes_count_30d,
                'conversion_pct_30d': conversion_pct_30d
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
                    'next_action': s_data.get('notes', ''),
                    'comments': s_data.get('comments') or '',
                    'response': s_data.get('response') or '',
                    'calc_method': 'Manual Target',
                    'is_locked': True,
                    'associated_count': 1,
                    'recent_orders_30d': ((recent_orders_30d_map.get(miss_int) or {}).get('value', 0) if miss_int is not None else 0),
                    'recent_orders_count_30d': ((recent_orders_30d_map.get(miss_int) or {}).get('count', 0) if miss_int is not None else 0),
                    'recent_quotes_30d': ((recent_quotes_30d_map.get(miss_int) or {}).get('value', 0) if miss_int is not None else 0),
                    'recent_quotes_count_30d': ((recent_quotes_30d_map.get(miss_int) or {}).get('count', 0) if miss_int is not None else 0),
                    'conversion_pct_30d': (
                        round((((recent_orders_30d_map.get(miss_int) or {}).get('value', 0)) / ((recent_quotes_30d_map.get(miss_int) or {}).get('value', 0))) * 100, 1)
                        if miss_int is not None and ((recent_quotes_30d_map.get(miss_int) or {}).get('value', 0) > 0) else None
                    )
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

        db = get_db_connection()

        next_action_provided = 'next_action' in data or 'notes' in data
        comments_provided = 'comments' in data
        response_provided = 'response' in data

        raw_next_action = data.get('next_action', data.get('notes', None))
        raw_comments = data.get('comments', None)
        raw_response = data.get('response', None)

        amount_provided = 'amount' in data
        raw_amount = data.get('amount')

        existing = None
        if not (amount_provided and next_action_provided and comments_provided and response_provided):
            existing = db.execute(
                """
                SELECT target_amount, notes, comments, response
                FROM customer_monthly_targets
                WHERE salesperson_id = ? AND customer_id = ? AND target_month = ?
                """,
                (salesperson_id, customer_id, target_month)
            ).fetchone()

        if amount_provided:
            if raw_amount == '' or raw_amount is None:
                amount = 0
            else:
                amount = float(raw_amount)
        else:
            amount = existing['target_amount'] if existing else 0

        if next_action_provided:
            notes = raw_next_action or ''
        else:
            notes = existing['notes'] if existing else ''

        if comments_provided:
            comments = raw_comments or ''
        else:
            comments = existing['comments'] if existing else ''

        if response_provided:
            response = raw_response or ''
        else:
            response = existing['response'] if existing else ''

        query = """
            INSERT INTO customer_monthly_targets
            (salesperson_id, customer_id, target_month, target_amount, notes, comments, response, is_locked, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(salesperson_id, customer_id, target_month)
            DO UPDATE SET 
                target_amount = excluded.target_amount,
                notes = excluded.notes,
                comments = excluded.comments,
                response = excluded.response,
                is_locked = 1,
                updated_at = CURRENT_TIMESTAMP
        """
        db.execute(query, (salesperson_id, customer_id, target_month, amount, notes, comments, response))
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


# -----------------------------------------------------------------------------
# CONSOLIDATED PLANNER (ALL SALESPEOPLE)
# -----------------------------------------------------------------------------
@salespeople_bp.route('/planner/consolidated')
@login_required
def consolidated_planner():
    """Renders the consolidated planner page showing all salespeople's plans"""
    # Get all active salespeople
    all_salespeople = get_all_salespeople_with_customer_counts()

    # Default to current month
    today = datetime.now().date()
    default_month = today.strftime('%Y-%m')

    return render_template(
        'salespeople/consolidated_planner.html',
        all_salespeople=all_salespeople,
        default_month=default_month
    )


@salespeople_bp.route('/planner/consolidated/data')
@login_required
def get_consolidated_planner_data():
    """Fetches planner data for all salespeople for a given month"""
    try:
        target_month_str = request.args.get('month')
        if not target_month_str:
            target_month_str = datetime.now().strftime('%Y-%m')

        target_date = datetime.strptime(target_month_str, '%Y-%m').date()

        # Get all salespeople
        all_salespeople = get_all_salespeople_with_customer_counts()

        consolidated_data = []

        for salesperson in all_salespeople:
            salesperson_id = salesperson['id']

            # Get consolidated customers for this salesperson
            consolidated_customers = get_consolidated_customer_ids(salesperson_id)

            # Collect all relevant customer IDs
            relevant_customer_ids = set()
            for main_id, group in consolidated_customers.items():
                relevant_customer_ids.update(group['all_customer_ids'])

            db = get_db_connection()

            # Fetch saved targets (same as individual planner)
            saved_targets = db.execute("""
                SELECT customer_id, target_amount, notes, is_locked
                FROM customer_monthly_targets
                WHERE salesperson_id = ? AND target_month = ?
            """, (salesperson_id, target_month_str)).fetchall()

            target_map = {
                row['customer_id']: {
                    'amount': float(row['target_amount'] or 0),
                    'next_action': row['notes'] or '',
                    'is_locked': bool(row['is_locked'])
                }
                for row in saved_targets
            }

            # Build customer_month_map using the EXACT same query as individual planner
            if _using_postgres():
                chart_date_expr = "to_char(date_entered, 'YYYY-MM')"
            else:
                chart_date_expr = "strftime('%Y-%m', date_entered)"

            customer_month_map = {}
            if relevant_customer_ids:
                placeholders = ','.join(['?' for _ in relevant_customer_ids])
                aggregated_history_query = f"""
                    SELECT customer_id, {chart_date_expr} as yyyy_mm,
                    SUM(CASE WHEN total_value IS NULL OR total_value::text = '' THEN 0 ELSE total_value END) as val
                    FROM sales_orders
                    WHERE customer_id IN ({placeholders})
                    GROUP BY customer_id, yyyy_mm
                """
                aggregated_rows = db_execute(aggregated_history_query, list(relevant_customer_ids), fetch='all') or []
                for row in aggregated_rows:
                    cust_id = row['customer_id']
                    customer_month_map.setdefault(cust_id, {})[row['yyyy_mm']] = float(row['val'] or 0)

            db.close()

            # Build customer list with targets and actuals (same logic as individual planner)
            customers = []
            today = datetime.now().date()
            three_months_ago = today - relativedelta(months=3)

            # Sort consolidated customers by size
            sorted_customers = sorted(
                consolidated_customers.items(),
                key=lambda item: len(item[1]['all_customer_ids']),
                reverse=True
            )

            processed_ids = set()

            for main_id, group in sorted_customers:
                str_main_id = str(main_id)

                if str_main_id in processed_ids:
                    continue

                processed_ids.add(str_main_id)
                for sub_id in group['all_customer_ids']:
                    processed_ids.add(str(sub_id))

                all_ids = group['all_customer_ids']
                sales_map = defaultdict(float)
                for sub_id in all_ids:
                    month_map = customer_month_map.get(sub_id, {})
                    for month_key, month_val in month_map.items():
                        sales_map[month_key] += month_val

                actual_sales = sales_map.get(target_month_str, 0)

                # Calculate recent average for target calculation
                recent_total = 0
                previous_active_total = 0
                for i in range(24, 0, -1):
                    d = today - relativedelta(months=i)
                    key = d.strftime('%Y-%m')
                    val = sales_map.get(key, 0)
                    if i <= 3:
                        recent_total += val
                    elif i <= 12:
                        previous_active_total += val

                recent_average = recent_total / 3

                # Check for saved target
                is_saved = str_main_id in target_map
                saved_data = target_map.get(str_main_id, {})

                # Calculate target (same logic as individual planner)
                if is_saved:
                    suggested_target = saved_data.get('amount', 0)
                    next_action = saved_data.get('next_action', '')
                else:
                    # Calculate target based on recent performance
                    if recent_average > 0:
                        suggested_target = round(recent_average * 1.1, -1)
                    elif previous_active_total > 0:
                        suggested_target = round((previous_active_total / 9), -1)
                    else:
                        suggested_target = 0
                    next_action = ''

                # Filter out very small customers (unless saved)
                if not is_saved and suggested_target < 100 and actual_sales < 100 and previous_active_total < 500:
                    continue

                customers.append({
                    'id': main_id,
                    'name': group['main_customer_name'],
                    'target': suggested_target,
                    'actual': actual_sales,
                    'next_action': next_action
                })

            # Calculate totals
            total_target = sum(c['target'] for c in customers)
            total_actual = sum(c['actual'] for c in customers)

            # Only include salespeople with customers
            if customers:
                consolidated_data.append({
                    'salesperson_id': salesperson_id,
                    'salesperson_name': salesperson['name'],
                    'customers': customers,
                    'total_target': total_target,
                    'total_actual': total_actual
                })

        return jsonify({
            'success': True,
            'data': consolidated_data,
            'month': target_month_str
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
