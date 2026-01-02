# Flask-related imports
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    current_app,
    Response,
    logging,
    current_app,

    send_from_directory
)
from flask import session
from flask_login import current_user
import base64
import quopri
from email.utils import parsedate_to_datetime
# Standard library imports
import os
import email  # Import the base email library
import imaplib
import re
import mimetypes
from datetime import date, datetime
from urllib.parse import quote, unquote, urlparse
from collections import defaultdict
import json
from functools import wraps
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone  # Add this import
from threading import Lock


# Email handling imports
from email.message import EmailMessage
from email.parser import BytesParser
from email.header import decode_header
from email import message
from email.parser import BytesParser  # Add this import
from datetime import datetime
import re
import email.utils

# File handling imports
from werkzeug.utils import secure_filename

# Third-party imports
from bs4 import BeautifulSoup
from dateutil import parser
import extract_msg  # For processing .msg email files
import msal
import requests

# Project-specific imports (replace with your actual module structure)
from db import db_cursor, execute as db_execute
from models import (
    get_contacts,
    save_email_log,
    insert_contact,
    get_customer_by_id,
    get_email_signature_by_id,
    insert_rfq,
    get_all_contacts,
    get_excess_stock_list_by_id,
    get_all_customers,
    get_template_by_id,
    get_contact_by_id,
    get_supplier_by_email,
    get_all_templates,
    get_customer_domains,
    get_supplier_domains,
    get_supplier_contact_by_email,
    get_contact_by_email
)
from models import create_base_part_number
from routes.email_signatures import get_user_default_signature
from hubspot_helpers import (
    get_or_create_hubspot_contact,
    get_or_create_hubspot_company,
    log_email_to_hubspot
)
from domains import populate_domains
from ai_helper import extract_part_numbers_and_quantities

emails_bp = Blueprint('emails', __name__)

MAILBOX_SETTING_KEYS = {
    "user": "mailbox_user",
    "password": "mailbox_password",
    "host": "mailbox_host",
    "port": "mailbox_port",
    "use_ssl": "mailbox_use_ssl",
}

GRAPH_SETTING_KEYS = {
    "client_id": "graph_client_id",
    "tenant_id": "graph_tenant_id",
    "client_secret": "graph_client_secret",
    "redirect_uri": "graph_redirect_uri",
    "scopes": "graph_scopes",
}

DEFAULT_GRAPH_SCOPES = [
    "User.Read",
    "Mail.Read",
    "Mail.ReadWrite",
    "Mail.Send",
]

GRAPH_DEFAULTS = {
    "client_id": "bbd6527a-1d84-40a5-94d3-cfef86cd0f29",
    "tenant_id": "e906849c-00a1-497a-95c4-38f844356d82",
}

RESERVED_GRAPH_SCOPES = {"offline_access", "profile", "openid"}

INLINE_ATTACHMENT_CACHE_TTL = 300
INLINE_ATTACHMENT_CACHE_MAX = 200
_INLINE_ATTACHMENT_CACHE = {}
_INLINE_ATTACHMENT_CACHE_LOCK = Lock()


def _get_app_setting(key, default=None):
    row = db_execute(
        "SELECT value FROM app_settings WHERE key = ?",
        (key,),
        fetch="one",
    )
    if not row:
        return default
    if isinstance(row, dict):
        return row.get("value", default)
    try:
        return row["value"]
    except Exception:
        return default


def _set_app_setting(key, value):
    db_execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value
        """,
        (key, value),
        commit=True,
    )


def _parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _get_default_signature(user_id=None):
    if user_id is None and current_user and getattr(current_user, "is_authenticated", False):
        user_id = current_user.id
    signature = get_user_default_signature(user_id) if user_id else None
    if signature:
        return signature
    return get_email_signature_by_id(1)


def _get_mailbox_settings(include_password=False):
    saved_user = _get_app_setting(MAILBOX_SETTING_KEYS["user"])
    saved_password = _get_app_setting(MAILBOX_SETTING_KEYS["password"])
    saved_host = _get_app_setting(MAILBOX_SETTING_KEYS["host"])
    saved_port = _get_app_setting(MAILBOX_SETTING_KEYS["port"])
    saved_ssl = _get_app_setting(MAILBOX_SETTING_KEYS["use_ssl"])

    email_host = saved_host or os.getenv("EMAIL_HOST", "")
    email_port = saved_port or os.getenv("EMAIL_PORT", "993")
    email_user = saved_user or os.getenv("EMAIL_USER", "")
    email_password = saved_password or os.getenv("EMAIL_PASSWORD", "")

    settings = {
        "email_user": email_user,
        "email_host": email_host,
        "email_port": str(email_port) if email_port is not None else "",
        "use_ssl": _parse_bool(saved_ssl, default=True),
        "password_set": bool(email_password),
    }

    if include_password:
        settings["email_password"] = email_password

    return settings


def _decode_imap_data(value):
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    if isinstance(value, (list, tuple)):
        return [_decode_imap_data(item) for item in value]
    return value


def _format_supplier_contact_name(contact):
    if not contact:
        return None
    first = contact.get("first_name") or ""
    second = contact.get("second_name") or ""
    full = f"{first} {second}".strip()
    return full or contact.get("name")


def _lookup_contact_company(email_address):
    if not email_address:
        return {
            "contact_name": None,
            "contact_type": None,
            "company_name": None,
            "company_type": None,
        }

    customer_contact = get_contact_by_email(email_address)
    if customer_contact:
        return {
            "contact_name": customer_contact.get("name"),
            "contact_type": "customer",
            "company_name": customer_contact.get("customer_name"),
            "company_type": "Customer",
        }

    supplier_contact = get_supplier_contact_by_email(email_address)
    if supplier_contact:
        return {
            "contact_name": _format_supplier_contact_name(supplier_contact),
            "contact_type": "supplier",
            "company_name": supplier_contact.get("supplier_name"),
            "company_type": "Supplier",
        }

    return {
        "contact_name": None,
        "contact_type": None,
        "company_name": None,
        "company_type": None,
    }


def _normalize_email_address(value):
    if not value:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _encode_graph_next_link(link):
    if not link:
        return None
    try:
        # Encode the full next link URL as base64 for safe transport
        encoded = base64.urlsafe_b64encode(link.encode("utf-8")).decode("ascii")
        # Remove padding to avoid issues with URL encoding
        return encoded.rstrip('=')
    except Exception:
        return None


def _decode_graph_next_link(token):
    if not token:
        return None
    try:
        # The token comes URL-encoded from the frontend, so decode it first
        normalized = unquote(token)
        # Add base64 padding if needed
        padding = (-len(normalized)) % 4
        normalized += "=" * padding
        return base64.urlsafe_b64decode(normalized.encode("ascii")).decode("utf-8")
    except Exception as e:
        # Log the error to help with debugging
        current_app.logger.error(f"Failed to decode pagination token: {e}, token: {token[:50] if token else None}")
        return None


def _extract_graph_addresses(recipients):
    emails = []
    for recipient in recipients or []:
        if isinstance(recipient, dict):
            email_data = recipient.get("emailAddress") or {}
            address = email_data.get("address") or recipient.get("address")
            normalized = _normalize_email_address(address)
            if normalized:
                emails.append(normalized)
    return emails


def _format_graph_datetime(value):
    if not value:
        return None
    try:
        parsed = parser.isoparse(value)
    except Exception:
        try:
            parsed = parser.parse(value)
        except Exception:
            return None
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _get_contacts_by_emails(emails):
    normalized = sorted({_normalize_email_address(email) for email in emails if email})
    normalized = [email for email in normalized if email]
    if not normalized:
        return {}
    placeholders = ",".join(["?"] * len(normalized))
    rows = db_execute(
        f"""
        SELECT c.*, cu.name as customer_name
        FROM contacts c
        LEFT JOIN customers cu ON c.customer_id = cu.id
        WHERE LOWER(c.email) IN ({placeholders})
          AND c.customer_id IS NOT NULL
        """,
        normalized,
        fetch="all",
    ) or []
    contacts = {}
    for row in rows:
        row_data = dict(row) if not isinstance(row, dict) else row
        email_value = _normalize_email_address(row_data.get("email"))
        if email_value:
            contacts[email_value] = row_data
    return contacts


def _build_graph_contact_entries(messages, mailbox_email):
    mailbox_email = _normalize_email_address(mailbox_email)
    entries = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        message_id = message.get("id")
        if not message_id:
            continue
        subject = message.get("subject") or ""
        from_data = message.get("from", {}).get("emailAddress", {}) if isinstance(message.get("from"), dict) else {}
        from_email = _normalize_email_address(from_data.get("address"))
        to_emails = _extract_graph_addresses(message.get("toRecipients"))
        cc_emails = _extract_graph_addresses(message.get("ccRecipients"))

        is_outbound = bool(from_email and mailbox_email and from_email == mailbox_email)
        if is_outbound:
            direction = "outbound"
            message_emails = [email for email in (to_emails + cc_emails) if email and email != mailbox_email]
            timestamp = _format_graph_datetime(message.get("sentDateTime") or message.get("receivedDateTime"))
        else:
            direction = "inbound"
            message_emails = [from_email] if from_email else []
            timestamp = _format_graph_datetime(message.get("receivedDateTime") or message.get("sentDateTime"))

        for email in message_emails:
            entries.append({
                "email_message_id": message_id,
                "contact_email": email,
                "direction": direction,
                "timestamp": timestamp,
                "subject": subject,
            })
    return entries


def _record_graph_contact_communications(messages, mailbox_email, salesperson_id):
    if not salesperson_id:
        return 0

    entries = _build_graph_contact_entries(messages, mailbox_email)
    if not entries:
        return 0

    contacts = _get_contacts_by_emails([entry["contact_email"] for entry in entries])
    if not contacts:
        return 0

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    insert_rows = []
    for entry in entries:
        contact = contacts.get(entry["contact_email"])
        if not contact:
            continue
        contact_id = contact.get("id")
        customer_id = contact.get("customer_id")
        if not contact_id or not customer_id:
            continue
        insert_rows.append((
            entry["timestamp"] or now_str,
            contact_id,
            customer_id,
            salesperson_id,
            "email",
            entry["subject"],
            entry["email_message_id"],
            entry["direction"],
        ))

    if not insert_rows:
        return 0

    if _using_postgres():
        insert_query = """
            INSERT INTO contact_communications
                (date, contact_id, customer_id, salesperson_id, communication_type, notes, email_message_id, email_direction)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (email_message_id, contact_id) DO NOTHING
        """
    else:
        insert_query = """
            INSERT OR IGNORE INTO contact_communications
                (date, contact_id, customer_id, salesperson_id, communication_type, notes, email_message_id, email_direction)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """

    with db_cursor(commit=True) as cursor:
        cursor.executemany(_prepare_placeholders(insert_query), insert_rows)
    return len(insert_rows)


def sync_graph_mailbox_emails():
    """
    Background job to sync and cache emails for all connected users.
    This should be run periodically (e.g., every 5-15 minutes) to keep the cache fresh.
    """
    settings = _get_graph_settings(include_secret=True)
    user_rows = db_execute("SELECT user_id FROM graph_token_cache", fetch="all") or []
    totals = {
        "users": 0,
        "messages_synced": 0,
        "errors": 0,
    }

    for row in user_rows:
        user_id = row.get("user_id") if isinstance(row, dict) else row[0]
        if not user_id:
            continue

        totals["users"] += 1

        try:
            cache = _load_graph_cache_for_user(user_id)
            app = _build_msal_app(settings, cache=cache)
            accounts = app.get_accounts()
            if not accounts:
                continue

            token = app.acquire_token_silent(settings["scopes"], account=accounts[0])
            _save_graph_cache_for_user(user_id, cache)

            if not token or "access_token" not in token:
                totals["errors"] += 1
                _update_sync_status(user_id, success=False, error="Failed to get access token")
                continue

            headers = {"Authorization": f"Bearer {token['access_token']}"}

            # Fetch latest emails (first 50)
            params = {
                "$top": "50",
                "$select": (
                    "id,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,"
                    "bodyPreview,webLink,conversationId,hasAttachments,isRead,importance"
                ),
                "$orderby": "receivedDateTime desc",
            }

            resp = requests.get(
                "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages",
                headers=headers,
                params=params,
                timeout=20,
            )

            if resp.status_code >= 400:
                totals["errors"] += 1
                _update_sync_status(user_id, success=False, error=f"Graph API error: {resp.status_code}")
                continue

            try:
                body = resp.json() if resp.content else {}
            except ValueError:
                totals["errors"] += 1
                _update_sync_status(user_id, success=False, error="Invalid JSON response")
                continue

            messages = body.get("value", []) if isinstance(body, dict) else []

            if messages:
                _cache_email_messages(user_id, messages)
                totals["messages_synced"] += len(messages)
                _update_sync_status(user_id, success=True)

        except Exception as exc:
            totals["errors"] += 1
            current_app.logger.error(f"Email sync failed for user {user_id}: {exc}")
            try:
                _update_sync_status(user_id, success=False, error=str(exc))
            except:
                pass

    return totals


def sync_graph_mailbox_contacts():
    settings = _get_graph_settings(include_secret=True)
    user_rows = db_execute("SELECT user_id FROM graph_token_cache", fetch="all") or []
    totals = {
        "users": 0,
        "messages": 0,
        "communications": 0,
        "errors": 0,
    }

    for row in user_rows:
        user_id = row.get("user_id") if isinstance(row, dict) else row[0]
        if not user_id:
            continue
        totals["users"] += 1
        try:
            cache = _load_graph_cache_for_user(user_id)
            app = _build_msal_app(settings, cache=cache)
            accounts = app.get_accounts()
            if not accounts:
                continue
            token = app.acquire_token_silent(settings["scopes"], account=accounts[0])
            _save_graph_cache_for_user(user_id, cache)
            if not token or "access_token" not in token:
                totals["errors"] += 1
                continue

            headers = {"Authorization": f"Bearer {token['access_token']}"}
            state = _get_graph_delta_state(user_id)
            delta_link = state.get("delta_link")
            mailbox_email = state.get("mailbox_email")
            if not mailbox_email:
                me_resp = requests.get(
                    "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName",
                    headers=headers,
                    timeout=20,
                )
                if me_resp.status_code < 400:
                    try:
                        me_body = me_resp.json() if me_resp.content else {}
                    except ValueError:
                        me_body = {}
                    mailbox_email = (me_body or {}).get("mail") or (me_body or {}).get("userPrincipalName")

            url = delta_link or "https://graph.microsoft.com/v1.0/me/messages/delta"
            params = None
            if not delta_link:
                params = {
                    "$top": "100",
                    "$select": (
                        "id,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,conversationId"
                    ),
                }

            salesperson_id = _get_salesperson_id_for_user(user_id) or user_id
            processed_messages = 0
            recorded = 0

            while url:
                resp = requests.get(url, headers=headers, params=params, timeout=30)
                params = None
                try:
                    body = resp.json() if resp.content else {}
                except ValueError:
                    body = {}
                if resp.status_code >= 400:
                    totals["errors"] += 1
                    break

                messages = body.get("value", []) if isinstance(body, dict) else []
                processed_messages += len(messages)
                if messages:
                    recorded += _record_graph_contact_communications(messages, mailbox_email, salesperson_id)

                next_link = body.get("@odata.nextLink") if isinstance(body, dict) else None
                delta_link = body.get("@odata.deltaLink") if isinstance(body, dict) else None
                if next_link:
                    url = next_link
                else:
                    url = None

            if delta_link or mailbox_email:
                _save_graph_delta_state(user_id, delta_link=delta_link, mailbox_email=mailbox_email)

            totals["messages"] += processed_messages
            totals["communications"] += recorded
        except Exception:
            totals["errors"] += 1

    return totals


def _get_graph_settings(include_secret=False):
    client_id = _get_app_setting(GRAPH_SETTING_KEYS["client_id"], "") or GRAPH_DEFAULTS["client_id"]
    tenant_id = _get_app_setting(GRAPH_SETTING_KEYS["tenant_id"], "") or GRAPH_DEFAULTS["tenant_id"]
    client_secret = _get_app_setting(GRAPH_SETTING_KEYS["client_secret"], "")
    redirect_uri = _get_app_setting(GRAPH_SETTING_KEYS["redirect_uri"], "")
    if not redirect_uri:
        redirect_uri = url_for('emails.graph_callback', _external=True)
    scopes_value = _get_app_setting(GRAPH_SETTING_KEYS["scopes"], "")
    if scopes_value:
        scopes = [s.strip() for s in scopes_value.split(",") if s.strip()]
    else:
        scopes = DEFAULT_GRAPH_SCOPES.copy()
    scopes = [scope for scope in scopes if scope not in RESERVED_GRAPH_SCOPES]

    settings = {
        "client_id": client_id,
        "tenant_id": tenant_id,
        "redirect_uri": redirect_uri,
        "scopes": scopes,
        "secret_set": bool(client_secret),
    }

    if include_secret:
        settings["client_secret"] = client_secret

    return settings


def _set_graph_settings(data):
    client_id = (data.get("client_id") or "").strip()
    tenant_id = (data.get("tenant_id") or "").strip()
    client_secret = data.get("client_secret")
    redirect_uri = (data.get("redirect_uri") or "").strip()
    scopes = data.get("scopes") or []
    if isinstance(scopes, str):
        scopes = [s.strip() for s in scopes.split(",") if s.strip()]
    scopes = [scope for scope in scopes if scope not in RESERVED_GRAPH_SCOPES]

    _set_app_setting(GRAPH_SETTING_KEYS["client_id"], client_id)
    _set_app_setting(GRAPH_SETTING_KEYS["tenant_id"], tenant_id)
    if client_secret is not None:
        _set_app_setting(GRAPH_SETTING_KEYS["client_secret"], client_secret)
    _set_app_setting(GRAPH_SETTING_KEYS["redirect_uri"], redirect_uri)
    _set_app_setting(GRAPH_SETTING_KEYS["scopes"], ",".join(scopes))

    return _get_graph_settings()


def _graph_authority(tenant_id):
    return f"https://login.microsoftonline.com/{tenant_id}"


def _load_graph_cache():
    cache = msal.SerializableTokenCache()
    serialized = None
    user_id = _current_graph_user_id()
    if user_id:
        row = db_execute(
            "SELECT cache_text FROM graph_token_cache WHERE user_id = ?",
            (user_id,),
            fetch="one",
        )
        if row:
            serialized = row.get("cache_text") if isinstance(row, dict) else row[0]
    else:
        serialized = session.get("graph_token_cache")
    if serialized:
        cache.deserialize(serialized)
    return cache


def _save_graph_cache(cache):
    if cache.has_state_changed:
        serialized = cache.serialize()
        user_id = _current_graph_user_id()
        if user_id:
            db_execute(
                """
                INSERT INTO graph_token_cache (user_id, cache_text, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE
                SET cache_text = EXCLUDED.cache_text,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, serialized),
                commit=True,
            )
        else:
            session["graph_token_cache"] = serialized


def _build_msal_app(settings, cache=None):
    if cache is None:
        cache = _load_graph_cache()
    return msal.ConfidentialClientApplication(
        settings["client_id"],
        authority=_graph_authority(settings["tenant_id"]),
        client_credential=settings.get("client_secret") or None,
        token_cache=cache,
    )


def _graph_recipient(address):
    normalized = _normalize_email_address(address)
    if not normalized:
        return None
    return {"emailAddress": {"address": normalized}}


def build_graph_inline_attachments():
    uploads_dir = os.path.join(current_app.root_path, "uploads")
    inline_specs = [
        {"filename": "blimage001.jpg", "content_id": "image001"},
        {"filename": "linkedin_icon.png", "content_id": "linkedin_icon"},
    ]
    attachments = []
    for spec in inline_specs:
        path = os.path.join(uploads_dir, spec["filename"])
        if not os.path.exists(path):
            continue
        with open(path, "rb") as img:
            img_data = img.read()
        content_type, _ = mimetypes.guess_type(path)
        attachments.append({
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": spec["filename"],
            "contentId": spec["content_id"],
            "contentType": content_type or "application/octet-stream",
            "isInline": True,
            "contentBytes": base64.b64encode(img_data).decode("ascii"),
        })
    return attachments


def send_graph_email(subject, html_body, to_emails, *, cc_emails=None, bcc_emails=None, attachments=None, user_id=None):
    settings = _get_graph_settings(include_secret=True)
    if not settings.get("client_id") or not settings.get("tenant_id") or not settings.get("client_secret"):
        return {"success": False, "error": "Graph settings are incomplete."}

    cache = _load_graph_cache_for_user(user_id) if user_id else _load_graph_cache()
    app = _build_msal_app(settings, cache=cache)
    accounts = app.get_accounts()
    if not accounts:
        return {"success": False, "error": "Graph mailbox is not connected for this user."}

    token = app.acquire_token_silent(settings["scopes"], account=accounts[0])
    if user_id:
        _save_graph_cache_for_user(user_id, cache)
    else:
        _save_graph_cache(cache)
    if not token or "access_token" not in token:
        return {"success": False, "error": token.get("error_description") or "Unable to get Graph access token."}

    to_list = [recipient for recipient in (_graph_recipient(addr) for addr in (to_emails or [])) if recipient]
    if not to_list:
        return {"success": False, "error": "No valid recipients provided."}

    message = {
        "subject": subject or "",
        "body": {"contentType": "HTML", "content": html_body or ""},
        "toRecipients": to_list,
    }

    if cc_emails:
        cc_list = [recipient for recipient in (_graph_recipient(addr) for addr in cc_emails) if recipient]
        if cc_list:
            message["ccRecipients"] = cc_list

    if bcc_emails:
        bcc_list = [recipient for recipient in (_graph_recipient(addr) for addr in bcc_emails) if recipient]
        if bcc_list:
            message["bccRecipients"] = bcc_list

    if attachments:
        message["attachments"] = attachments

    headers = {
        "Authorization": f"Bearer {token['access_token']}",
        "Content-Type": "application/json",
    }
    payload = {"message": message, "saveToSentItems": True}
    resp = requests.post(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        headers=headers,
        json=payload,
        timeout=20,
    )
    if resp.status_code >= 400:
        try:
            error_body = resp.json()
        except ValueError:
            error_body = {}
        graph_error = error_body.get("error", {}) if isinstance(error_body, dict) else {}
        message_text = graph_error.get("message") or resp.text or "Graph send failed."
        return {"success": False, "error": message_text, "status_code": resp.status_code}

    return {"success": True}


def _load_graph_cache_for_user(user_id):
    cache = msal.SerializableTokenCache()
    row = db_execute(
        "SELECT cache_text FROM graph_token_cache WHERE user_id = ?",
        (user_id,),
        fetch="one",
    )
    if row:
        serialized = row.get("cache_text") if isinstance(row, dict) else row[0]
        if serialized:
            cache.deserialize(serialized)
    return cache


def _save_graph_cache_for_user(user_id, cache):
    if not cache.has_state_changed:
        return
    serialized = cache.serialize()
    db_execute(
        """
        INSERT INTO graph_token_cache (user_id, cache_text, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE
        SET cache_text = EXCLUDED.cache_text,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, serialized),
        commit=True,
    )


def _current_graph_user_id():
    if current_user and getattr(current_user, "is_authenticated", False):
        return getattr(current_user, "id", None)
    return None


def _get_salesperson_id_for_user(user_id):
    row = db_execute(
        "SELECT legacy_salesperson_id FROM salesperson_user_link WHERE user_id = ?",
        (user_id,),
        fetch="one",
    )
    if not row:
        return None
    return row.get("legacy_salesperson_id") if isinstance(row, dict) else row[0]


def _get_graph_delta_state(user_id):
    row = db_execute(
        """
        SELECT delta_link, mailbox_email
        FROM graph_mailbox_deltas
        WHERE user_id = ?
        """,
        (user_id,),
        fetch="one",
    )
    if not row:
        return {"delta_link": None, "mailbox_email": None}
    return row if isinstance(row, dict) else {"delta_link": row[0], "mailbox_email": row[1]}


def _save_graph_delta_state(user_id, delta_link=None, mailbox_email=None):
    db_execute(
        """
        INSERT INTO graph_mailbox_deltas (user_id, delta_link, mailbox_email, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE
        SET delta_link = EXCLUDED.delta_link,
            mailbox_email = EXCLUDED.mailbox_email,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, delta_link, mailbox_email),
        commit=True,
    )


def _cache_email_messages(user_id, messages):
    """Cache email messages to the database for faster loading"""
    if not messages or not user_id:
        return

    for msg in messages:
        if not isinstance(msg, dict) or not msg.get('id'):
            continue

        message_id = msg.get('id')
        from_data = msg.get('from', {})
        sender_addr = from_data.get('emailAddress', {}) if isinstance(from_data, dict) else {}

        db_execute(
            """
            INSERT INTO graph_email_cache (
                user_id, message_id, conversation_id, subject, sender_name, sender_email,
                received_datetime, sent_datetime, body_preview, web_link,
                from_data, to_recipients, cc_recipients, has_attachments, is_read,
                importance, raw_message, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, message_id) DO UPDATE
            SET conversation_id = EXCLUDED.conversation_id,
                subject = EXCLUDED.subject,
                sender_name = EXCLUDED.sender_name,
                sender_email = EXCLUDED.sender_email,
                received_datetime = EXCLUDED.received_datetime,
                sent_datetime = EXCLUDED.sent_datetime,
                body_preview = EXCLUDED.body_preview,
                web_link = EXCLUDED.web_link,
                from_data = EXCLUDED.from_data,
                to_recipients = EXCLUDED.to_recipients,
                cc_recipients = EXCLUDED.cc_recipients,
                has_attachments = EXCLUDED.has_attachments,
                is_read = EXCLUDED.is_read,
                importance = EXCLUDED.importance,
                raw_message = EXCLUDED.raw_message,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                user_id,
                message_id,
                msg.get('conversationId'),
                msg.get('subject'),
                sender_addr.get('name') if isinstance(sender_addr, dict) else None,
                sender_addr.get('address') if isinstance(sender_addr, dict) else None,
                msg.get('receivedDateTime'),
                msg.get('sentDateTime'),
                msg.get('bodyPreview'),
                msg.get('webLink'),
                json.dumps(from_data) if from_data else None,
                json.dumps(msg.get('toRecipients', [])),
                json.dumps(msg.get('ccRecipients', [])),
                True if msg.get('hasAttachments') else False,
                True if msg.get('isRead') else False,
                msg.get('importance'),
                json.dumps(msg),
            ),
            commit=True,
        )


def _get_cached_emails(user_id, limit=25, offset=0):
    """Retrieve cached emails from database"""
    if not user_id:
        return []

    rows = db_execute(
        """
        SELECT raw_message, id, message_id
        FROM graph_email_cache
        WHERE user_id = ?
        ORDER BY received_datetime DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, limit, offset),
        fetch="all",
    )

    if not rows:
        return []

    messages = []
    for row in rows:
        raw_msg = row.get('raw_message') if isinstance(row, dict) else row[0]
        if raw_msg:
            try:
                messages.append(json.loads(raw_msg))
            except (json.JSONDecodeError, TypeError):
                pass

    return messages


def _update_sync_status(user_id, success=True, error=None, delta_link=None):
    """Update the sync status for a user"""
    if not user_id:
        return

    # Count total cached messages
    count_row = db_execute(
        "SELECT COUNT(*) as count FROM graph_email_cache WHERE user_id = ?",
        (user_id,),
        fetch="one",
    )
    total = count_row.get('count', 0) if isinstance(count_row, dict) else (count_row[0] if count_row else 0)

    db_execute(
        """
        INSERT INTO graph_email_sync_status (
            user_id, last_sync_at, last_sync_success, last_sync_error,
            delta_link, total_cached_messages, updated_at
        ) VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE
        SET last_sync_at = CURRENT_TIMESTAMP,
            last_sync_success = EXCLUDED.last_sync_success,
            last_sync_error = EXCLUDED.last_sync_error,
            delta_link = COALESCE(EXCLUDED.delta_link, delta_link),
            total_cached_messages = EXCLUDED.total_cached_messages,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, True if success else False, error, delta_link, total),
        commit=True,
    )


def _using_postgres() -> bool:
    url = os.getenv('DATABASE_URL', '')
    return url.startswith(('postgres://', 'postgresql://'))


def _prepare_placeholders(query: str) -> str:
    if _using_postgres():
        return query.replace('?', '%s')
    return query


def _execute_with_cursor(cur, query, params=None, *, fetch=None):
    cur.execute(_prepare_placeholders(query), params or [])
    if fetch == 'one':
        return cur.fetchone()
    if fetch == 'all':
        return cur.fetchall()
    return cur


def _normalize_content_id(value):
    if not value:
        return None
    value = str(value).strip()
    if value.startswith('<') and value.endswith('>'):
        value = value[1:-1]
    return value.lower()


def _get_inline_attachment_cache(message_id):
    if not message_id:
        return None
    now = time.time()
    with _INLINE_ATTACHMENT_CACHE_LOCK:
        entry = _INLINE_ATTACHMENT_CACHE.get(message_id)
        if not entry:
            return None
        if now - entry["ts"] > INLINE_ATTACHMENT_CACHE_TTL:
            _INLINE_ATTACHMENT_CACHE.pop(message_id, None)
            return None
        return entry["attachments"]


def _set_inline_attachment_cache(message_id, attachments):
    if not message_id:
        return
    now = time.time()
    with _INLINE_ATTACHMENT_CACHE_LOCK:
        _INLINE_ATTACHMENT_CACHE[message_id] = {"ts": now, "attachments": attachments}
        if len(_INLINE_ATTACHMENT_CACHE) <= INLINE_ATTACHMENT_CACHE_MAX:
            return
        oldest = sorted(_INLINE_ATTACHMENT_CACHE.items(), key=lambda item: item[1]["ts"])
        for key, _value in oldest[: max(0, len(oldest) - INLINE_ATTACHMENT_CACHE_MAX)]:
            _INLINE_ATTACHMENT_CACHE.pop(key, None)


def _fetch_email_associations(message_ids, conversation_ids, table_name):
    if not message_ids and not conversation_ids:
        return {}, {}
    if table_name not in {"tickets", "parts_lists"}:
        return {}, {}

    clauses = []
    params = []
    if message_ids:
        clauses.append(f"email_message_id IN ({','.join(['?'] * len(message_ids))})")
        params.extend(message_ids)
    if conversation_ids:
        clauses.append(f"email_conversation_id IN ({','.join(['?'] * len(conversation_ids))})")
        params.extend(conversation_ids)

    where_clause = " OR ".join(clauses)
    try:
        rows = db_execute(
            f"""
            SELECT id, email_message_id, email_conversation_id
            FROM {table_name}
            WHERE {where_clause}
            """,
            params,
            fetch="all",
        ) or []
    except Exception:
        return {}, {}

    by_message = {}
    by_conversation = {}
    for row in rows:
        row_data = dict(row) if not isinstance(row, dict) else row
        message_id = row_data.get("email_message_id")
        conversation_id = row_data.get("email_conversation_id")
        if message_id:
            by_message[message_id] = row_data.get("id")
        if conversation_id:
            by_conversation[conversation_id] = row_data.get("id")
    return by_message, by_conversation


def _find_parts_lists_for_parts(part_numbers, supplier_id=None, limit=15):
    """
    Return candidate parts lists that contain any of the provided part numbers.
    Uses both the raw customer part number and the normalized base part number.
    """
    if not part_numbers:
        return []

    clean_numbers = []
    base_numbers = []
    for pn in part_numbers:
        if not pn:
            continue
        pn_str = str(pn).strip()
        if not pn_str:
            continue
        clean_numbers.append(pn_str.upper())
        try:
            base_numbers.append(create_base_part_number(pn_str))
        except Exception:
            base_numbers.append(pn_str.upper())

    if not clean_numbers and not base_numbers:
        return []

    conditions = []
    params = []
    if clean_numbers:
        placeholders = ",".join(["?"] * len(clean_numbers))
        conditions.append(f"UPPER(pll.customer_part_number) IN ({placeholders})")
        params.extend(clean_numbers)
    if base_numbers:
        placeholders = ",".join(["?"] * len(base_numbers))
        conditions.append(f"pll.base_part_number IN ({placeholders})")
        params.extend(base_numbers)

    if not conditions:
        return []

    supplier_param = supplier_id if supplier_id is not None else -1
    params_with_supplier = [supplier_param] + params + [supplier_param, limit]

    rows = db_execute(
        f"""
        SELECT 
            pl.id AS parts_list_id,
            pl.name AS parts_list_name,
            COALESCE(c.name, '') AS customer_name,
            COUNT(DISTINCT pll.id) AS match_count,
            COUNT(DISTINCT CASE WHEN se.supplier_id = ? THEN pll.id END) AS supplier_request_count,
            MAX(pl.date_modified) AS last_modified
        FROM parts_list_lines pll
        JOIN parts_lists pl ON pl.id = pll.parts_list_id
        LEFT JOIN customers c ON c.id = pl.customer_id
        LEFT JOIN parts_list_line_supplier_emails se ON se.parts_list_line_id = pll.id
        WHERE ({' OR '.join(conditions)})
        GROUP BY pl.id, pl.name, customer_name
        ORDER BY supplier_request_count DESC, match_count DESC, last_modified DESC
        LIMIT ?
        """,
        params_with_supplier,
        fetch="all",
    )

    results = []
    for row in rows or []:
        record = dict(row)
        if supplier_id:
            record["quick_quote_url"] = url_for(
                "parts_list.quick_supplier_quote",
                list_id=record["parts_list_id"],
                supplier_id=supplier_id,
            )
        results.append(record)
    return results


# Helper function to get company name by the sender's email
def get_company_name_by_email(sender_email):
    # Look up customer contact
    customer_contact, customer = None, None
    contacts = get_contacts()
    for contact in contacts:
        if contact['email'] == sender_email:
            customer_contact = contact
            customer = get_customer_by_id(contact['customer_id'])
            break

    # Look up supplier contact
    supplier_contact = get_supplier_contact_by_email(sender_email)

    # Return all found information
    return {
        'customer_contact': customer_contact,
        'customer': customer,
        'supplier_contact': supplier_contact,
        'supplier_name': supplier_contact['supplier_name'] if supplier_contact else None
    }



from dateutil import parser
import re

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'eml', 'msg'}


def decode_encoded_words(text):
    """Decode RFC 2047 encoded words in a string."""
    if not text:
        return ""

    # Pattern to detect encoded-words format =?charset?encoding?encoded-text?=
    pattern = r'=\?(.+?)\?([BQ])\?(.+?)\?='

    def decode_match(match):
        charset, encoding, encoded_text = match.groups()
        if encoding.upper() == 'B':
            # Base64 encoding
            try:
                return base64.b64decode(encoded_text).decode(charset)
            except:
                return encoded_text  # Return original if decode fails
        elif encoding.upper() == 'Q':
            # Quoted-printable encoding
            try:
                return quopri.decodestring(encoded_text.encode()).decode(charset)
            except:
                return encoded_text  # Return original if decode fails
        return encoded_text  # Fallback

    import re
    return re.sub(pattern, decode_match, text)

@emails_bp.route('/emails')
def list_emails():
    mailbox = _get_mailbox_settings()
    graph = _get_graph_settings()
    return render_template('emails.html', mailbox=mailbox, graph=graph)


@emails_bp.route('/emails/mailbox')
def mailbox_page():
    graph = _get_graph_settings()
    graph_user = session.get("graph_last_user")
    return render_template('emails_mailbox.html', graph=graph, graph_user=graph_user)


@emails_bp.route('/emails/mailbox-settings', methods=['GET', 'POST'])
def mailbox_settings():
    if request.method == 'GET':
        settings = _get_mailbox_settings()
        return jsonify({'success': True, 'settings': settings})

    data = request.get_json(silent=True) or {}

    email_user = (data.get('email_user') or '').strip()
    email_password = data.get('email_password')
    email_host = (data.get('email_host') or '').strip()
    email_port = (data.get('email_port') or '').strip()
    use_ssl = _parse_bool(data.get('use_ssl'), default=True)

    _set_app_setting(MAILBOX_SETTING_KEYS["user"], email_user)
    if email_password is not None:
        _set_app_setting(MAILBOX_SETTING_KEYS["password"], email_password)
    _set_app_setting(MAILBOX_SETTING_KEYS["host"], email_host)
    _set_app_setting(MAILBOX_SETTING_KEYS["port"], email_port)
    _set_app_setting(MAILBOX_SETTING_KEYS["use_ssl"], "1" if use_ssl else "0")

    settings = _get_mailbox_settings()
    return jsonify({'success': True, 'settings': settings})


@emails_bp.route('/emails/test-connection', methods=['POST'])
def test_mailbox_connection():
    started_at = time.time()
    data = request.get_json(silent=True) or {}
    saved_settings = _get_mailbox_settings(include_password=True)

    email_user = (data.get('email_user') or saved_settings.get("email_user") or "").strip()
    email_password = data.get('email_password')
    if email_password is None:
        email_password = saved_settings.get("email_password") or ""
    email_host = (data.get('email_host') or saved_settings.get("email_host") or "").strip()
    email_port = data.get('email_port') or saved_settings.get("email_port") or "993"
    use_ssl = _parse_bool(data.get('use_ssl'), default=saved_settings.get("use_ssl", True))

    debug_info = {
        "input": {
            "email_user": email_user,
            "email_host": email_host,
            "email_port": str(email_port),
            "use_ssl": use_ssl,
            "password_set": bool(email_password),
            "password_length": len(email_password) if email_password else 0,
        },
        "events": [],
    }

    missing = []
    if not email_host:
        missing.append("email_host")
    if not email_user:
        missing.append("email_user")
    if not email_password:
        missing.append("email_password")

    if missing:
        return jsonify({
            "success": False,
            "error": {
                "message": "Missing required settings",
                "missing": missing,
            },
            "debug": debug_info,
        }), 400

    mail = None
    try:
        debug_info["events"].append({
            "ts": datetime.utcnow().isoformat() + "Z",
            "step": "connect",
            "detail": {
                "host": email_host,
                "port": int(email_port),
                "use_ssl": use_ssl,
            },
        })

        if use_ssl:
            mail = imaplib.IMAP4_SSL(email_host, int(email_port))
        else:
            mail = imaplib.IMAP4(email_host, int(email_port))

        debug_info["events"].append({
            "ts": datetime.utcnow().isoformat() + "Z",
            "step": "login",
            "detail": {"user": email_user},
        })
        mail.login(email_user, email_password)

        debug_info["events"].append({
            "ts": datetime.utcnow().isoformat() + "Z",
            "step": "list_folders",
        })
        status, folders = mail.list()
        debug_info["events"].append({
            "ts": datetime.utcnow().isoformat() + "Z",
            "step": "list_folders_result",
            "detail": {
                "status": status,
                "folders": _decode_imap_data(folders[:20] if folders else []),
            },
        })

        debug_info["events"].append({
            "ts": datetime.utcnow().isoformat() + "Z",
            "step": "select_inbox",
        })
        status, data = mail.select("INBOX")
        debug_info["events"].append({
            "ts": datetime.utcnow().isoformat() + "Z",
            "step": "select_inbox_result",
            "detail": {
                "status": status,
                "data": _decode_imap_data(data),
            },
        })

        duration_ms = int((time.time() - started_at) * 1000)
        return jsonify({
            "success": True,
            "duration_ms": duration_ms,
            "debug": debug_info,
        })
    except Exception as exc:
        duration_ms = int((time.time() - started_at) * 1000)
        return jsonify({
            "success": False,
            "duration_ms": duration_ms,
            "error": {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
            "debug": debug_info,
        }), 500
    finally:
        try:
            if mail is not None:
                mail.logout()
        except Exception:
            pass


@emails_bp.route('/emails/graph/settings', methods=['GET', 'POST'])
def graph_settings():
    if request.method == 'GET':
        settings = _get_graph_settings()
        return jsonify({'success': True, 'settings': settings})

    data = request.get_json(silent=True) or {}
    settings = _set_graph_settings(data)
    return jsonify({'success': True, 'settings': settings})


@emails_bp.route('/emails/graph/connect', methods=['GET'])
def graph_connect():
    settings = _get_graph_settings(include_secret=True)
    missing = []
    if not settings.get("client_id"):
        missing.append("client_id")
    if not settings.get("tenant_id"):
        missing.append("tenant_id")
    if not settings.get("client_secret"):
        missing.append("client_secret")

    redirect_uri = settings.get("redirect_uri") or url_for('emails.graph_callback', _external=True)
    settings["redirect_uri"] = redirect_uri

    if missing:
        return jsonify({
            "success": False,
            "error": {
                "message": "Missing Graph settings",
                "missing": missing,
            },
        }), 400

    state = uuid.uuid4().hex
    next_url = request.args.get("next") or url_for('emails.list_emails')
    session["graph_auth_state"] = state
    session["graph_redirect_uri"] = redirect_uri
    session["graph_next_url"] = next_url

    cache = _load_graph_cache()
    app = _build_msal_app(settings, cache=cache)
    auth_url = app.get_authorization_request_url(
        settings["scopes"],
        state=state,
        redirect_uri=redirect_uri,
        prompt="select_account",
    )
    _save_graph_cache(cache)
    return redirect(auth_url)


@emails_bp.route('/emails/graph/callback', methods=['GET'])
def graph_callback():
    error = request.args.get("error")
    if error:
        return jsonify({
            "success": False,
            "error": {
                "type": error,
                "message": request.args.get("error_description") or "Authorization failed",
            },
        }), 400

    state = request.args.get("state")
    saved_state = session.get("graph_auth_state")
    if not state or state != saved_state:
        if current_app.debug:
            current_app.logger.warning(
                "Graph state mismatch in debug; continuing. expected=%s received=%s",
                saved_state,
                state,
            )
        else:
            return jsonify({
                "success": False,
                "error": {
                    "message": "State mismatch",
                },
            }), 400

    code = request.args.get("code")
    if not code:
        return jsonify({
            "success": False,
            "error": {
                "message": "Missing authorization code",
            },
        }), 400

    settings = _get_graph_settings(include_secret=True)
    redirect_uri = session.get("graph_redirect_uri") or settings.get("redirect_uri") or url_for('emails.graph_callback', _external=True)
    settings["redirect_uri"] = redirect_uri

    cache = _load_graph_cache()
    app = _build_msal_app(settings, cache=cache)
    result = app.acquire_token_by_authorization_code(
        code,
        scopes=settings["scopes"],
        redirect_uri=redirect_uri,
    )
    _save_graph_cache(cache)

    if "error" in result:
        return jsonify({
            "success": False,
            "error": {
                "type": result.get("error"),
                "message": result.get("error_description") or "Token acquisition failed",
                "correlation_id": result.get("correlation_id"),
            },
            "debug": result,
        }), 400

    session["graph_last_user"] = result.get("id_token_claims", {}).get("preferred_username")

    next_url = session.pop("graph_next_url", None) or url_for('emails.list_emails')
    return redirect(next_url)


@emails_bp.route('/emails/graph/test', methods=['POST'])
def graph_test():
    settings = _get_graph_settings(include_secret=True)
    cache = _load_graph_cache()
    app = _build_msal_app(settings, cache=cache)
    accounts = app.get_accounts()

    if not accounts:
        return jsonify({
            "success": False,
            "error": {
                "message": "No Graph account connected. Click Connect with Microsoft first.",
            },
        }), 400

    token = app.acquire_token_silent(settings["scopes"], account=accounts[0])
    _save_graph_cache(cache)

    if not token or "access_token" not in token:
        return jsonify({
            "success": False,
            "error": {
                "message": "Failed to refresh access token",
            },
            "debug": token,
        }), 400

    access_token = token["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    debug = {"calls": []}

    me_resp = requests.get("https://graph.microsoft.com/v1.0/me", headers=headers, timeout=20)
    try:
        me_body = me_resp.json() if me_resp.content else None
    except ValueError:
        me_body = me_resp.text
    debug["calls"].append({
        "endpoint": "/me",
        "status": me_resp.status_code,
        "body": me_body,
    })

    messages_resp = requests.get(
        "https://graph.microsoft.com/v1.0/me/messages?$top=5",
        headers=headers,
        timeout=20,
    )
    try:
        messages_body = messages_resp.json() if messages_resp.content else None
    except ValueError:
        messages_body = messages_resp.text
    debug["calls"].append({
        "endpoint": "/me/messages?$top=5",
        "status": messages_resp.status_code,
        "body": messages_body,
    })

    return jsonify({
        "success": True,
        "debug": debug,
    })


@emails_bp.route('/emails/graph/sync-cache', methods=['POST'])
def graph_sync_cache():
    """Manual endpoint to trigger email cache refresh"""
    user_id = _current_graph_user_id()
    if not user_id:
        return jsonify({
            "success": False,
            "error": "User not authenticated",
        }), 401

    try:
        # Run sync for just this user
        settings = _get_graph_settings(include_secret=True)
        cache = _load_graph_cache()
        app = _build_msal_app(settings, cache=cache)
        accounts = app.get_accounts()

        if not accounts:
            return jsonify({
                "success": False,
                "error": "No Graph account connected",
            }), 400

        token = app.acquire_token_silent(settings["scopes"], account=accounts[0])
        _save_graph_cache(cache)

        if not token or "access_token" not in token:
            return jsonify({
                "success": False,
                "error": "Failed to get access token",
            }), 400

        headers = {"Authorization": f"Bearer {token['access_token']}"}
        params = {
            "$top": "50",
            "$select": (
                "id,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,"
                "bodyPreview,webLink,conversationId,hasAttachments,isRead,importance"
            ),
            "$orderby": "receivedDateTime desc",
        }

        resp = requests.get(
            "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages",
            headers=headers,
            params=params,
            timeout=20,
        )

        if resp.status_code >= 400:
            return jsonify({
                "success": False,
                "error": f"Graph API error: {resp.status_code}",
            }), 400

        body = resp.json() if resp.content else {}
        messages = body.get("value", []) if isinstance(body, dict) else []

        if messages:
            _cache_email_messages(user_id, messages)
            _update_sync_status(user_id, success=True)

        return jsonify({
            "success": True,
            "messages_synced": len(messages),
        })

    except Exception as exc:
        current_app.logger.error(f"Manual sync failed: {exc}")
        _update_sync_status(user_id, success=False, error=str(exc))
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 500


@emails_bp.route('/emails/graph/messages', methods=['GET'])
def graph_messages():
    settings = _get_graph_settings(include_secret=True)
    cache = _load_graph_cache()
    app = _build_msal_app(settings, cache=cache)
    accounts = app.get_accounts()

    if not accounts:
        return jsonify({
            "success": False,
            "error": {
                "message": "No Graph account connected. Click Connect with Microsoft first.",
            },
        }), 400

    token = app.acquire_token_silent(settings["scopes"], account=accounts[0])
    _save_graph_cache(cache)

    if not token or "access_token" not in token:
        return jsonify({
            "success": False,
            "error": {
                "message": "Failed to refresh access token",
            },
            "debug": token,
        }), 400

    headers = {"Authorization": f"Bearer {token['access_token']}"}
    page_size = request.args.get("page_size", "25")
    try:
        page_size = int(page_size)
    except (TypeError, ValueError):
        page_size = 25
    page_size = max(1, min(page_size, 50))

    page_token = request.args.get("page_token")
    user_id = _current_graph_user_id()
    use_cache_only = request.args.get("use_cache", "").lower() == "true"

    # If no page_token and not explicitly forcing cache, try to serve from cache first
    if not page_token and not use_cache_only and user_id:
        cached_messages = _get_cached_emails(user_id, limit=page_size)
        if cached_messages:
            # We have cached data, enrich it and return
            message_ids = [m.get("id") for m in cached_messages if isinstance(m, dict) and m.get("id")]
            conversation_ids = [m.get("conversationId") for m in cached_messages if isinstance(m, dict) and m.get("conversationId")]
            ticket_by_message, ticket_by_conversation = _fetch_email_associations(message_ids, conversation_ids, "tickets")
            parts_list_by_message, parts_list_by_conversation = _fetch_email_associations(message_ids, conversation_ids, "parts_lists")

            for message in cached_messages:
                from_data = message.get("from", {}).get("emailAddress", {}) if isinstance(message, dict) else {}
                from_email = from_data.get("address")
                message["from_email"] = from_email
                message["from_name"] = from_data.get("name")
                message["lookup"] = _lookup_contact_company(from_email)
                message_id = message.get("id")
                conversation_id = message.get("conversationId")
                message["ticket_id"] = ticket_by_message.get(message_id) or ticket_by_conversation.get(conversation_id)
                message["parts_list_id"] = parts_list_by_message.get(message_id) or parts_list_by_conversation.get(conversation_id)

            # Check if there are more cached messages
            count_row = db_execute(
                "SELECT COUNT(*) as count FROM graph_email_cache WHERE user_id = ?",
                (user_id,),
                fetch="one",
            )
            total_cached = count_row.get('count', 0) if isinstance(count_row, dict) else (count_row[0] if count_row else 0)
            has_more = total_cached > page_size

            return jsonify({
                "success": True,
                "messages": cached_messages,
                "next_token": "cached_page_2" if has_more else None,
                "from_cache": True,
            })

    # If we're here, fetch from API
    next_link = _decode_graph_next_link(page_token) if page_token else None
    if page_token and not next_link:
        return jsonify({
            "success": False,
            "error": {
                "message": "Invalid pagination token",
            },
        }), 400
    if next_link:
        parsed_next = urlparse(next_link)
        if (
            parsed_next.scheme != "https"
            or parsed_next.netloc != "graph.microsoft.com"
            or not (
                parsed_next.path.startswith("/v1.0/me/mailFolders")
                or parsed_next.path.startswith("/v1.0/me/messages")
            )
        ):
            return jsonify({
                "success": False,
                "error": {
                    "message": "Invalid pagination token",
                },
            }), 400
        resp = requests.get(next_link, headers=headers, timeout=20)
    else:
        params = {
            "$top": str(page_size),
            "$select": (
                "id,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,"
                "bodyPreview,webLink,conversationId"
            ),
            "$orderby": "receivedDateTime desc",
        }
        resp = requests.get(
            "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages",
            headers=headers,
            params=params,
            timeout=20,
        )
    try:
        body = resp.json() if resp.content else None
    except ValueError:
        body = resp.text

    if resp.status_code >= 400:
        return jsonify({
            "success": False,
            "error": {
                "message": "Graph request failed",
                "status": resp.status_code,
            },
            "debug": body,
        }), 400

    messages = body.get("value", []) if isinstance(body, dict) else []
    next_token = None
    if isinstance(body, dict):
        next_token = _encode_graph_next_link(body.get("@odata.nextLink"))

    mailbox_email = session.get("graph_last_user")
    if not mailbox_email:
        me_resp = requests.get(
            "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName",
            headers=headers,
            timeout=20,
        )
        if me_resp.status_code < 400:
            try:
                me_body = me_resp.json() if me_resp.content else {}
            except ValueError:
                me_body = {}
            mailbox_email = (me_body or {}).get("mail") or (me_body or {}).get("userPrincipalName")
            if mailbox_email:
                session["graph_last_user"] = mailbox_email

    message_ids = [m.get("id") for m in messages if isinstance(m, dict) and m.get("id")]
    conversation_ids = [m.get("conversationId") for m in messages if isinstance(m, dict) and m.get("conversationId")]
    ticket_by_message, ticket_by_conversation = _fetch_email_associations(message_ids, conversation_ids, "tickets")
    parts_list_by_message, parts_list_by_conversation = _fetch_email_associations(message_ids, conversation_ids, "parts_lists")

    for message in messages:
        from_data = message.get("from", {}).get("emailAddress", {}) if isinstance(message, dict) else {}
        from_email = from_data.get("address")
        message["from_email"] = from_email
        message["from_name"] = from_data.get("name")
        message["lookup"] = _lookup_contact_company(from_email)
        message_id = message.get("id")
        conversation_id = message.get("conversationId")
        message["ticket_id"] = ticket_by_message.get(message_id) or ticket_by_conversation.get(conversation_id)
        message["parts_list_id"] = parts_list_by_message.get(message_id) or parts_list_by_conversation.get(conversation_id)

    try:
        salesperson_id = None
        if hasattr(current_user, "get_salesperson_id"):
            salesperson_id = current_user.get_salesperson_id()
        if not salesperson_id:
            salesperson_id = getattr(current_user, "id", None)
        _record_graph_contact_communications(messages, mailbox_email, salesperson_id)
    except Exception as exc:
        current_app.logger.warning("Graph contact sync failed: %s", exc)

    # Cache the fetched messages for faster loading next time
    if user_id and messages and not page_token:
        try:
            _cache_email_messages(user_id, messages)
            _update_sync_status(user_id, success=True)
        except Exception as exc:
            current_app.logger.warning("Failed to cache emails: %s", exc)

    return jsonify({
        "success": True,
        "messages": messages,
        "next_token": next_token,
    })


@emails_bp.route('/emails/graph/message/<path:message_id>', methods=['GET'])
def graph_message_detail(message_id):
    settings = _get_graph_settings(include_secret=True)
    cache = _load_graph_cache()
    app = _build_msal_app(settings, cache=cache)
    accounts = app.get_accounts()

    if not accounts:
        return jsonify({
            "success": False,
            "error": {
                "message": "No Graph account connected. Click Connect with Microsoft first.",
            },
        }), 400

    token = app.acquire_token_silent(settings["scopes"], account=accounts[0])
    _save_graph_cache(cache)

    if not token or "access_token" not in token:
        return jsonify({
            "success": False,
            "error": {
                "message": "Failed to refresh access token",
            },
            "debug": token,
        }), 400

    headers = {"Authorization": f"Bearer {token['access_token']}"}
    params = {
        "$select": "id,subject,from,receivedDateTime,body,bodyPreview,webLink,conversationId",
    }
    safe_message_id = quote(message_id, safe="")
    resp = requests.get(
        f"https://graph.microsoft.com/v1.0/me/messages/{safe_message_id}",
        headers=headers,
        params=params,
        timeout=20,
    )
    try:
        body = resp.json() if resp.content else None
    except ValueError:
        body = resp.text

    if resp.status_code >= 400:
        return jsonify({
            "success": False,
            "error": {
                "message": "Graph request failed",
                "status": resp.status_code,
            },
            "debug": body,
        }), 400

    from_data = body.get("from", {}).get("emailAddress", {}) if isinstance(body, dict) else {}
    from_email = from_data.get("address")
    lookup = _lookup_contact_company(from_email)

    return jsonify({
        "success": True,
        "message": body,
        "lookup": lookup,
    })


@emails_bp.route('/emails/graph/message/<path:message_id>/inline-attachments', methods=['GET'])
def graph_message_inline_attachments(message_id):
    cached = _get_inline_attachment_cache(message_id)
    if cached is not None:
        return jsonify({
            "success": True,
            "attachments": cached,
            "cached": True,
        })
    settings = _get_graph_settings(include_secret=True)
    cache = _load_graph_cache()
    app = _build_msal_app(settings, cache=cache)
    accounts = app.get_accounts()

    if not accounts:
        return jsonify({
            "success": False,
            "error": {
                "message": "No Graph account connected. Click Connect with Microsoft first.",
            },
        }), 400

    token = app.acquire_token_silent(settings["scopes"], account=accounts[0])
    _save_graph_cache(cache)

    if not token or "access_token" not in token:
        return jsonify({
            "success": False,
            "error": {
                "message": "Failed to refresh access token",
            },
            "debug": token,
        }), 400

    headers = {"Authorization": f"Bearer {token['access_token']}"}
    params = {
        "$select": "id,name,contentType,isInline",
    }
    safe_message_id = quote(message_id, safe="")
    resp = requests.get(
        f"https://graph.microsoft.com/v1.0/me/messages/{safe_message_id}/attachments",
        headers=headers,
        params=params,
        timeout=20,
    )
    try:
        body = resp.json() if resp.content else None
    except ValueError:
        body = resp.text

    if resp.status_code >= 400:
        return jsonify({
            "success": False,
            "error": {
                "message": "Graph request failed",
                "status": resp.status_code,
            },
            "debug": body,
        }), 400

    attachments = []
    for item in body.get("value", []) if isinstance(body, dict) else []:
        if not item.get("isInline"):
            continue
        attachment_id = item.get("id")
        if not attachment_id:
            continue
        safe_attachment_id = quote(attachment_id, safe="")
        detail_resp = requests.get(
            f"https://graph.microsoft.com/v1.0/me/messages/{safe_message_id}/attachments/{safe_attachment_id}",
            headers=headers,
            timeout=20,
        )
        try:
            detail_body = detail_resp.json() if detail_resp.content else None
        except ValueError:
            detail_body = None
        if detail_resp.status_code >= 400 or not isinstance(detail_body, dict):
            continue
        content_id = detail_body.get("contentId")
        content_bytes = detail_body.get("contentBytes")
        if not content_id or not content_bytes:
            continue
        content_type = detail_body.get("contentType") or item.get("contentType") or "application/octet-stream"
        attachments.append({
            "content_id": content_id,
            "content_id_key": _normalize_content_id(content_id),
            "data_url": f"data:{content_type};base64,{content_bytes}",
        })

    _set_inline_attachment_cache(message_id, attachments)
    return jsonify({
        "success": True,
        "attachments": attachments,
    })

@emails_bp.route('/emails/graph/message/<path:message_id>/attachments', methods=['GET'])
def graph_message_attachments(message_id):
    settings = _get_graph_settings(include_secret=True)
    cache = _load_graph_cache()
    app = _build_msal_app(settings, cache=cache)
    accounts = app.get_accounts()

    if not accounts:
        return jsonify({
            "success": False,
            "error": {
                "message": "No Graph account connected. Click Connect with Microsoft first.",
            },
        }), 400

    token = app.acquire_token_silent(settings["scopes"], account=accounts[0])
    _save_graph_cache(cache)

    if not token or "access_token" not in token:
        return jsonify({
            "success": False,
            "error": {
                "message": "Failed to refresh access token",
            },
            "debug": token,
        }), 400

    headers = {"Authorization": f"Bearer {token['access_token']}"}
    params = {
        "$select": "id,name,contentType,size,isInline",
    }
    safe_message_id = quote(message_id, safe="")
    resp = requests.get(
        f"https://graph.microsoft.com/v1.0/me/messages/{safe_message_id}/attachments",
        headers=headers,
        params=params,
        timeout=20,
    )
    try:
        body = resp.json() if resp.content else None
    except ValueError:
        body = resp.text

    if resp.status_code >= 400:
        return jsonify({
            "success": False,
            "error": {
                "message": "Graph request failed",
                "status": resp.status_code,
            },
            "debug": body,
        }), 400

    attachments = []
    for item in body.get("value", []) if isinstance(body, dict) else []:
        name = item.get("name") or ""
        content_type = item.get("contentType") or ""
        is_pdf = name.lower().endswith(".pdf") or content_type == "application/pdf"
        attachments.append({
            "id": item.get("id"),
            "name": name,
            "content_type": content_type,
            "size": item.get("size"),
            "is_inline": item.get("isInline"),
            "is_pdf": is_pdf,
        })

    return jsonify({
        "success": True,
        "attachments": attachments,
    })


@emails_bp.route('/emails/graph/message/<path:message_id>/attachments/<path:attachment_id>', methods=['GET'])
def graph_message_attachment_content(message_id, attachment_id):
    settings = _get_graph_settings(include_secret=True)
    cache = _load_graph_cache()
    app = _build_msal_app(settings, cache=cache)
    accounts = app.get_accounts()

    if not accounts:
        return jsonify({
            "success": False,
            "error": {
                "message": "No Graph account connected. Click Connect with Microsoft first.",
            },
        }), 400

    token = app.acquire_token_silent(settings["scopes"], account=accounts[0])
    _save_graph_cache(cache)

    if not token or "access_token" not in token:
        return jsonify({
            "success": False,
            "error": {
                "message": "Failed to refresh access token",
            },
            "debug": token,
        }), 400

    headers = {"Authorization": f"Bearer {token['access_token']}"}
    safe_message_id = quote(message_id, safe="")
    safe_attachment_id = quote(attachment_id, safe="")
    resp = requests.get(
        f"https://graph.microsoft.com/v1.0/me/messages/{safe_message_id}/attachments/{safe_attachment_id}",
        headers=headers,
        timeout=20,
    )
    try:
        body = resp.json() if resp.content else None
    except ValueError:
        body = resp.text

    if resp.status_code >= 400:
        return jsonify({
            "success": False,
            "error": {
                "message": "Graph request failed",
                "status": resp.status_code,
            },
            "debug": body,
        }), 400

    if not isinstance(body, dict):
        return jsonify({
            "success": False,
            "error": {
                "message": "Invalid attachment response",
            },
        }), 400

    content_bytes = body.get("contentBytes")
    if not content_bytes:
        return jsonify({
            "success": False,
            "error": {
                "message": "Attachment has no content",
            },
        }), 400

    return jsonify({
        "success": True,
        "attachment": {
            "id": body.get("id"),
            "name": body.get("name"),
            "content_type": body.get("contentType"),
            "size": body.get("size"),
            "content_bytes": content_bytes,
        },
    })

from bs4 import BeautifulSoup



# Clean unnecessary tags from HTML content but preserve <br> and <p>
def clean_html_email_content(email_content):
    soup = BeautifulSoup(email_content, "html.parser")

    # Remove unnecessary tags like style, meta, office-specific tags
    for tag in soup(["style", "meta", "head", "xml", "o:p", "vlink", "link", "script"]):
        tag.decompose()  # Remove these tags

    # Optionally, clean extra spaces or empty tags
    for p_tag in soup.find_all('p'):
        if not p_tag.get_text(strip=True):  # Remove empty <p> tags
            p_tag.decompose()

    # Return the cleaned HTML as a string
    return str(soup)


@emails_bp.route('/create_rfq_from_email/<email_id>', methods=['POST'])
def create_rfq_from_email(email_id):
    print(f"Starting RFQ creation for email {email_id}")

    # Connect to email server and get the email
    email_host = os.getenv('EMAIL_HOST')
    email_port = int(os.getenv('EMAIL_PORT', 993))
    email_user = os.getenv('EMAIL_USER')
    email_password = os.getenv('EMAIL_PASSWORD')

    try:
        print("Connecting to email server")
        # Connect to email server
        mail = imaplib.IMAP4_SSL(email_host, email_port)
        mail.login(email_user, email_password)
        mail.select("inbox")

        # Fetch the specific email
        print(f"Fetching email {email_id}")
        res, msg = mail.fetch(email_id.encode(), "(RFC822)")
        email_message = None
        for response_part in msg:
            if isinstance(response_part, tuple):
                email_message = email.message_from_bytes(response_part[1])
                break

        if not email_message:
            print("No email found")
            flash('Email not found', 'error')
            return redirect(url_for('emails.list_emails'))

        # Get email details
        subject = decode_header(email_message["Subject"])[0][0]
        if isinstance(subject, bytes):
            subject = subject.decode()
        sender = email_message.get("From")
        sender_email = sender.split('<')[-1].replace('>', '').strip()

        print(f"Processing email from {sender_email}")

        # Get the customer information
        result = get_company_name_by_email(sender_email)
        customer_contact = result['customer_contact']

        if not customer_contact:
            print(f"No customer contact found for {sender_email}")
            flash('No customer contact found for this email address', 'error')
            return redirect(url_for('emails.list_emails'))

        print(f"Found customer contact: {customer_contact}")

        try:
            with db_cursor(commit=True) as cursor:
                customer = _execute_with_cursor(
                    cursor,
                    'SELECT * FROM customers WHERE id = ?',
                    (customer_contact['customer_id'],),
                    fetch='one',
                )

                if not customer:
                    print(f"No customer found for contact {customer_contact['id']}")
                    flash('Customer not found', 'error')
                    return redirect(url_for('emails.list_emails'))

                print(f"Found customer: {customer['name']}")

                # Extract email content
                email_content = None
                if email_message.is_multipart():
                    html_content = None
                    plain_content = None

                    for part in email_message.walk():
                        if part.get_content_type() == "text/html":
                            payload = part.get_payload(decode=True)
                            if payload:
                                try:
                                    html_content = payload.decode('utf-8')
                                except UnicodeDecodeError:
                                    try:
                                        html_content = payload.decode('iso-8859-1')
                                    except UnicodeDecodeError:
                                        html_content = payload.decode('windows-1252')
                        elif part.get_content_type() == "text/plain":
                            payload = part.get_payload(decode=True)
                            if payload:
                                try:
                                    plain_content = payload.decode('utf-8')
                                except UnicodeDecodeError:
                                    try:
                                        plain_content = payload.decode('iso-8859-1')
                                    except UnicodeDecodeError:
                                        plain_content = payload.decode('windows-1252')

                    email_content = html_content if html_content else plain_content
                else:
                    payload = email_message.get_payload(decode=True)
                    if payload:
                        try:
                            email_content = payload.decode('utf-8')
                        except UnicodeDecodeError:
                            try:
                                email_content = payload.decode('iso-8859-1')
                            except UnicodeDecodeError:
                                email_content = payload.decode('windows-1252')

                print("Creating RFQ record")
                entered_date = date.today().isoformat()

                inserted_rfq = _execute_with_cursor(
                    cursor,
                    '''
                    INSERT INTO rfqs (
                        entered_date,
                        customer_id,
                        customer_ref,
                        contact_id,
                        email,
                        currency,
                        status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    ''',
                    (
                        entered_date,
                        customer['id'],
                        subject,
                        customer_contact['id'],
                        email_content,
                        3,
                        'new',
                    ),
                    fetch='one',
                )

                rfq_id = inserted_rfq['id'] if inserted_rfq else None
                print(f"Created RFQ with ID: {rfq_id}")

                attachment_count = 0
                for part in email_message.walk():
                    if part.get_content_maintype() == 'multipart':
                        continue
                    if part.get('Content-Disposition') is None:
                        continue

                    filename = part.get_filename()
                    if filename:
                        filename = secure_filename(filename)
                        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)

                        with open(filepath, 'wb') as f:
                            f.write(part.get_payload(decode=True))

                        file_row = _execute_with_cursor(
                            cursor,
                            '''
                            INSERT INTO files (filename, filepath, upload_date)
                            VALUES (?, ?, ?)
                            RETURNING id
                            ''',
                            (filename, filepath, datetime.now()),
                            fetch='one',
                        )
                        file_id = file_row['id']

                        _execute_with_cursor(
                            cursor,
                            '''
                            INSERT INTO rfq_files (rfq_id, file_id)
                            VALUES (?, ?)
                            ''',
                            (rfq_id, file_id),
                        )

                        attachment_count += 1

                print(f"Processed {attachment_count} attachments")

            flash('RFQ created successfully from email', 'success')
            print(f"Redirecting to RFQ edit page for RFQ {rfq_id}")
            return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

        except Exception as e:
            print(f"Database error: {str(e)}")
            flash(f'Error creating RFQ from email: {str(e)}', 'error')
            return redirect(url_for('emails.list_emails'))

    except Exception as e:
        print(f"Error creating RFQ: {str(e)}")
        flash(f'Error creating RFQ from email: {str(e)}', 'error')
        return redirect(url_for('emails.list_emails'))

    finally:
        if 'mail' in locals():
            mail.logout()

# Updated route to return full email details
@emails_bp.route('/emails/content/<email_id>')
def get_email_content(email_id):
    email_host = os.getenv('EMAIL_HOST')
    email_port = int(os.getenv('EMAIL_PORT', 993))
    email_user = os.getenv('EMAIL_USER')
    email_password = os.getenv('EMAIL_PASSWORD')

    mail = imaplib.IMAP4_SSL(email_host, email_port)
    mail.login(email_user, email_password)
    mail.select("inbox")

    # Fetch the email by ID
    res, msg = mail.fetch(email_id, "(RFC822)")
    email_content = None
    subject = sender = date = ""
    for response_part in msg:
        if isinstance(response_part, tuple):
            msg = email.message_from_bytes(response_part[1])

            # Get the email headers (subject, sender, date)
            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding if encoding else 'utf-8')
            sender = msg.get("From")
            date = msg.get("Date")

            # Get the email content (body)
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        email_content = part.get_payload(decode=True).decode()
                        break
            else:
                email_content = msg.get_payload(decode=True).decode()

    mail.logout()

    # Return email details as JSON
    return jsonify({
        'subject': subject,
        'sender': sender,
        'date': date,
        'content': email_content
    })

@emails_bp.route('/upload_email/<string:entity>/<int:entity_id>', methods=['POST'])
def upload_email(entity, entity_id):
    # Ensure it's for excess stock lists
    if entity != 'excess_list':
        flash('Invalid entity!', 'error')
        return redirect(request.referrer)

    # Fetch the excess list by ID
    excess_list = get_excess_stock_list_by_id(entity_id)
    if not excess_list:
        flash('Excess list not found!', 'error')
        return redirect(request.referrer)

    # Check if an email file is present in the request
    if 'email_file' not in request.files:
        flash('No file part', 'error')
        return redirect(request.referrer)

    file = request.files['email_file']
    if file.filename == '':
        flash('No selected file', 'error')
        return redirect(request.referrer)

    # Process the uploaded email file
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        wipe_existing = str(request.form.get('wipe_existing', '')).lower() in ('1', 'true', 'on', 'yes')

        try:
            # Save the file to the uploads folder
            file.save(file_path)

            # Process the .msg or .eml file
            msg = extract_msg.Message(file_path)  # Assuming .msg files are used

            # Extract email body and save to the excess_stock_lists table
            email_content = msg.htmlBody if msg.htmlBody else msg.body

            with db_cursor(commit=True) as cursor:
                if wipe_existing:
                    _execute_with_cursor(
                        cursor,
                        'DELETE FROM excess_stock_lines WHERE excess_stock_list_id = ?',
                        (entity_id,),
                    )
                    _execute_with_cursor(
                        cursor,
                        'DELETE FROM excess_stock_files WHERE excess_stock_list_id = ?',
                        (entity_id,),
                    )
                    _execute_with_cursor(
                        cursor,
                        'UPDATE excess_stock_lists SET email = NULL WHERE id = ?',
                        (entity_id,),
                    )

                _execute_with_cursor(
                    cursor,
                    'UPDATE excess_stock_lists SET email = ? WHERE id = ?',
                    (email_content, entity_id),
                )

                for attachment in msg.attachments:
                    attachment_name = attachment.longFilename if attachment.longFilename else attachment.shortFilename
                    attachment_data = attachment.data

                    attachment_path = os.path.join(current_app.config['UPLOAD_FOLDER'], secure_filename(attachment_name))
                    with open(attachment_path, 'wb') as f:
                        f.write(attachment_data)

                    file_row = _execute_with_cursor(
                        cursor,
                        'INSERT INTO files (filename, filepath, upload_date) VALUES (?, ?, ?) RETURNING id',
                        (attachment_name, attachment_path, datetime.now()),
                        fetch='one',
                    )
                    file_id = file_row['id']

                    _execute_with_cursor(
                        cursor,
                        'INSERT INTO excess_stock_files (excess_stock_list_id, file_id) VALUES (?, ?)',
                        (entity_id, file_id),
                    )

            flash('Email and attachments uploaded and processed successfully!', 'success')

        except Exception as e:
            flash(f'Error processing email: {str(e)}', 'error')
    else:
        flash('Invalid file type. Only .eml and .msg files are allowed.', 'error')

    return redirect(request.referrer)

@emails_bp.route('/upload_email2/excess_list/<int:entity_id>', methods=['POST'])
def upload_email2(entity_id):
    # Check if an email file is present in the request
    if 'email_file' not in request.files:
        flash('No file part', 'error')
        current_app.logger.error("No file part found in the request")
        return redirect(request.referrer)

    file = request.files['email_file']
    if file.filename == '':
        flash('No selected file', 'error')
        current_app.logger.error("No file selected")
        return redirect(request.referrer)

    # Log the received file
    current_app.logger.debug(f"Received file: {file.filename}")

    # Process the uploaded email file
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)

        # Log the save path
        current_app.logger.debug(f"Saving email to: {file_path}")

        try:
            # Save the file to the uploads folder
            file.save(file_path)

            # Now process the saved file and extract the email content
            msg = extract_msg.Message(file_path)
            email_content = msg.htmlBody if msg.htmlBody else msg.body

            # Log extracted content
            current_app.logger.debug(f"Extracted email content: {email_content[:50]}")  # Log first 100 characters

            db_execute(
                'UPDATE excess_stock_lists SET email = ? WHERE id = ?',
                (email_content, entity_id),
                commit=True,
            )

            flash('Email and attachments uploaded and processed successfully!', 'success')
        except Exception as e:
            flash(f'Error processing email: {str(e)}', 'error')
            current_app.logger.error(f"Error processing email: {str(e)}")

    return redirect(request.referrer)


@emails_bp.route('/view_email_frame/<string:entity>/<int:entity_id>', methods=['GET'])
def view_email_frame(entity, entity_id):
    # Ensure it's for excess stock lists
    if entity != 'excess_list':
        return "Invalid entity.", 400

    email_content = db_execute(
        'SELECT email FROM excess_stock_lists WHERE id = ?',
        (entity_id,),
        fetch='one',
    )

    if not email_content or not email_content['email']:
        return "No email content available.", 200

    attachments = db_execute(
        '''
        SELECT filename 
        FROM files 
        JOIN excess_stock_files ON files.id = excess_stock_files.file_id 
        WHERE excess_stock_files.excess_stock_list_id = ?
        ''',
        (entity_id,),
        fetch='all',
    )

    # Decode the email content if necessary
    email_body = email_content['email']
    if isinstance(email_body, bytes):
        email_body = email_body.decode('utf-8', errors='ignore')

    email_body = email_body.strip()

    # Generate the HTML for attachments
    attachment_html = ""
    if attachments:
        attachment_html = "<h3>Attachments:</h3><ul>"
        for attachment in attachments:
            attachment_html += f"<li><a href='/static/uploads/{attachment['filename']}' target='_blank'>{attachment['filename']}</a></li>"
        attachment_html += "</ul>"

    # Return the email content along with attachments
    return f"<div>{email_body}</div><div>{attachment_html}</div>"


@emails_bp.route('/view_email/<string:entity>/<int:entity_id>', methods=['GET'])
def view_email(entity, entity_id):
    # Ensure it's for excess stock lists
    if entity != 'excess_list':
        flash('Invalid entity!', 'error')
        return redirect(request.referrer)

    email_content = db_execute(
        'SELECT email FROM excess_stock_lists WHERE id = ?',
        (entity_id,),
        fetch='one',
    )

    if not email_content or not email_content['email']:
        flash('Email not found!', 'error')
        return redirect(request.referrer)

    email_body = email_content['email']
    if isinstance(email_body, bytes):
        email_body = email_body.decode('utf-8', errors='ignore')

    email_body = email_body.strip()

    attachments = db_execute(
        '''
        SELECT filename 
        FROM files 
        JOIN excess_stock_files ON files.id = excess_stock_files.file_id 
        WHERE excess_stock_files.excess_stock_list_id = ?
        ''',
        (entity_id,),
        fetch='all',
    )

    # Render the email content and attachments
    return render_template('view_email.html', html_body=email_body, attachments=attachments)


@emails_bp.route('/check_email', methods=['POST'])
def check_email():
    data = request.json
    sender_email = data.get('sender_email')

    # Logging the original sender email
    current_app.logger.info(f"Original email received: {sender_email}")

    # Strip out the name and keep only the email address if it's in the format "Name <email>"
    if '<' in sender_email and '>' in sender_email:
        sender_email = sender_email.split('<')[-1].replace('>', '').strip()

    # Logging the cleaned email
    current_app.logger.info(f"Cleaned email to check: {sender_email}")

    # Check if the email belongs to a customer
    contact, customer = get_company_name_by_email(sender_email)
    if customer:
        current_app.logger.info(f"Customer found: {customer['id']}")
        return jsonify({"customer_id": customer['id']}), 200

    # Check if the email belongs to a supplier
    supplier = get_supplier_by_email(sender_email)
    if supplier:
        current_app.logger.info(f"Supplier found: {supplier['id']}")
        return jsonify({"supplier_id": supplier['id']}), 200

    # Log when no match is found
    current_app.logger.info(f"No customer or supplier found for email: {sender_email}")
    return jsonify({"error": "Email not found"}), 404


@emails_bp.route('/emails/suppliers/<int:supplier_id>/outstanding-requests', methods=['GET'])
def get_supplier_outstanding_requests(supplier_id):
    """
    Return parts lists where this supplier has been emailed but has not yet provided a price or no-bid.
    """
    try:
        rows = db_execute(
            """
            WITH sent AS (
                SELECT 
                    pll.parts_list_id,
                    COALESCE(pll.parent_line_id, pll.id) AS quote_line_id,
                    MAX(se.date_sent) AS last_sent
                FROM parts_list_line_supplier_emails se
                JOIN parts_list_lines pll ON pll.id = se.parts_list_line_id
                WHERE se.supplier_id = ?
                GROUP BY pll.parts_list_id, quote_line_id
            ),
            responses AS (
                SELECT 
                    sql.parts_list_line_id,
                    COALESCE(sql.is_no_bid, FALSE) AS is_no_bid,
                    sql.unit_price
                FROM parts_list_supplier_quote_lines sql
                JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                WHERE sq.supplier_id = ?
            )
            SELECT 
                s.parts_list_id,
                pl.name AS parts_list_name,
                COALESCE(c.name, '') AS customer_name,
                COUNT(*) AS request_lines,
                COUNT(CASE WHEN r.unit_price IS NOT NULL AND COALESCE(r.is_no_bid, FALSE) = FALSE THEN 1 END) AS quoted_lines,
                COUNT(CASE WHEN COALESCE(r.is_no_bid, FALSE) = TRUE THEN 1 END) AS no_bid_lines,
                COUNT(CASE WHEN r.unit_price IS NULL AND COALESCE(r.is_no_bid, FALSE) = FALSE THEN 1 END) AS awaiting_lines,
                MAX(s.last_sent) AS last_sent
            FROM sent s
            LEFT JOIN responses r ON r.parts_list_line_id = s.quote_line_id
            JOIN parts_lists pl ON pl.id = s.parts_list_id
            LEFT JOIN customers c ON c.id = pl.customer_id
            GROUP BY s.parts_list_id, pl.name, customer_name
            HAVING COUNT(CASE WHEN r.unit_price IS NULL AND COALESCE(r.is_no_bid, FALSE) = FALSE THEN 1 END) > 0
            ORDER BY awaiting_lines DESC, last_sent DESC
            """,
            (supplier_id, supplier_id),
            fetch="all",
        )

        requests_data = []
        for row in rows or []:
            record = dict(row)
            record["quick_quote_url"] = url_for(
                "parts_list.quick_supplier_quote",
                list_id=record["parts_list_id"],
                supplier_id=supplier_id,
            )
            requests_data.append(record)

        return jsonify(
            success=True,
            supplier_id=supplier_id,
            requests=requests_data,
        )
    except Exception as exc:
        current_app.logger.error("Failed to load supplier outstanding requests: %s", exc)
        return jsonify(success=False, error=str(exc)), 500


@emails_bp.route('/macro_upload_email/<int:excess_list_id>', methods=['GET', 'POST'])
def macro_upload_email(excess_list_id):
    # Check if the list exists
    excess_list = get_excess_stock_list_by_id(excess_list_id)
    if not excess_list:
        return f"Excess list with ID {excess_list_id} not found", 404

    if request.method == 'GET':
        return f"Ready to upload file for list ID: {excess_list_id}", 200

    # Handle file upload in POST request
    if request.method == 'POST':
        # Your file upload logic here
        return f"File upload logic for list ID: {excess_list_id}", 200

@emails_bp.route('/emails/send_test_email', methods=['POST'])
def send_test_email():
    try:
        data = request.get_json(silent=True) or {}
        to_address = (data.get("to_address") or "").strip()
        if not to_address:
            to_address = (session.get("graph_last_user") or "").strip()
        if not to_address:
            return jsonify({'success': False, 'error': 'Missing test recipient email'}), 400

        subject = data.get("subject") or "Test Email from CRM"
        html_content = data.get("html_body") or "<p>This is a test email sent from your CRM.</p>"
        result = send_graph_email(
            subject=subject,
            html_body=html_content,
            to_emails=[to_address],
        )
        if not result.get("success"):
            return jsonify({'success': False, 'error': result.get("error", "Graph send failed")}), 500

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@emails_bp.route('/emails/mailbox/scan-parts', methods=['POST'])
def mailbox_scan_parts():
    """
    On-demand AI extraction of part numbers/quantities from mailbox emails.
    Returns candidate parts lists that include those parts to help narrow the match.
    """
    try:
        data = request.get_json(force=True) or {}
        raw_text = (data.get("text") or "").strip()
        supplier_id = data.get("supplier_id")

        try:
            supplier_id = int(supplier_id) if supplier_id is not None else None
        except (TypeError, ValueError):
            supplier_id = None

        if not raw_text:
            return jsonify(success=False, error="Email text is required"), 400

        # Keep the payload tight for the AI call
        max_chars = 8000
        text = raw_text[:max_chars]

        extracted = extract_part_numbers_and_quantities(text) or []

        parts = []
        seen = set()
        for part_number, qty in extracted:
            if not part_number:
                continue
            normalized = str(part_number).strip()
            if not normalized:
                continue
            if normalized.upper() in seen:
                continue
            seen.add(normalized.upper())
            quantity_val = None
            try:
                quantity_val = int(qty)
            except Exception:
                quantity_val = qty
            parts.append({
                "part_number": normalized,
                "quantity": quantity_val,
            })

        suggestions = _find_parts_lists_for_parts(
            [p["part_number"] for p in parts],
            supplier_id=supplier_id,
            limit=20,
        ) if parts else []

        return jsonify(
            success=True,
            parts=parts,
            suggestions=suggestions,
            truncated=len(raw_text) > max_chars,
        )
    except Exception as exc:
        current_app.logger.error("Mailbox part scan failed: %s", exc)
        return jsonify(success=False, error=str(exc)), 500

import os
from flask import send_from_directory

@emails_bp.route('/uploads/<filename>')
def uploaded_file(filename):
    uploads_dir = os.path.join(current_app.root_path, 'uploads')
    return send_from_directory(uploads_dir, filename)

@emails_bp.route('/emails/build_from_template/<int:template_id>', methods=['GET', 'POST'])
def build_email_from_template(template_id):
    template = get_template_by_id(template_id)
    if not template:
        flash('Template not found!', 'error')
        return redirect(url_for('templates.list_templates'))

    if request.method == 'POST':
        if 'preview' in request.form:
            # Get form data
            contact_id = request.form.get('contact_id')
            customer_id = request.form.get('customer_id')

            # Get related objects
            contact = get_contact_by_id(contact_id) if contact_id else None
            customer = get_customer_by_id(customer_id) if customer_id else None

            if not contact:
                flash('Please select a contact', 'error')
                return redirect(url_for('emails.build_email_from_template', template_id=template_id))

            # Process template
            subject = template['subject']
            body = template['body']

            # Replace placeholders
            if customer:
                subject = subject.replace('{{company_name}}', customer['name'])
                body = body.replace('{{company_name}}', customer['name'])

            if contact:
                body = body.replace('{{contact_name}}', contact['name'])
                body = body.replace('{{contact_first_name}}', contact['name'].split()[0])
                body = body.replace('{{contact_title}}', contact.get('job_title') or '')

            body = body.replace('{{sender_name}}', "Tom Palmer")
            body = body.replace('{{sender_title}}', "Sales Manager")
            body = body.replace('{{today_date}}', datetime.now().strftime('%Y-%m-%d'))

            # Fetch email signature by ID 1
            email_signature = _get_default_signature()
            if email_signature:
                # Convert CID references to actual image URLs for preview
                signature_html = email_signature['signature_html']
                signature_html = signature_html.replace('cid:image001', url_for('emails.uploaded_file', filename='blimage001.jpg'))
                signature_html = signature_html.replace('cid:linkedin_icon', url_for('emails.uploaded_file', filename='linkedin_icon.png'))
                body += f"\n\n{signature_html}"

            return render_template('emails/preview_email.html',
                                   template=template,
                                   contact=contact,
                                   subject=subject,
                                   body=body)

    # GET request
    customers = get_all_customers()
    contacts = get_all_contacts()

    return render_template('emails/build_from_template.html',
                           template=template,
                           customers=customers,
                           contacts=contacts)

@emails_bp.route('/emails/send_from_template/<int:template_id>', methods=['POST'])
def send_email_from_template(template_id):
    """
    Send an email using a template with proper HTML formatting and embedded images
    """
    try:
        bcc_email = "145554557@bcc.eu1.hubspot.com"

        # Get template and form data
        template = get_template_by_id(template_id)
        if not template:
            return jsonify({'success': False, 'error': 'Template not found'})

        # Get form data
        contact_id = request.form.get('contact_id')
        customer_id = request.form.get('customer_id')

        # Validate required data
        if not contact_id:
            return jsonify({'success': False, 'error': 'Contact is required'})

        # Get related objects
        contact = get_contact_by_id(contact_id)
        customer = get_customer_by_id(customer_id) if customer_id else None

        if not contact:
            return jsonify({'success': False, 'error': 'Contact not found'})

        # Process template
        subject = template['subject']
        body = template['body'].replace('\n', '<br>')
        body = f"""
        <html>
            <head>
                <style>
                    p {{ margin: 0 0 1em 0; }}
                    br {{ margin-bottom: 0.5em; }}
                </style>
            </head>
            <body>
                <div style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6;">
                    {body}
                </div>
            </body>
        </html>
        """

        # Handle template replacements
        if customer:
            customer_name = customer.get('name', '')
            subject = subject.replace('{{company_name}}', str(customer_name))
            body = body.replace('{{company_name}}', str(customer_name))
        else:
            subject = subject.replace('{{company_name}}', '')
            body = body.replace('{{company_name}}', '')

        contact_name = contact.get('name', '')
        contact_first_name = contact_name.split()[0] if contact_name else ''
        contact_title = contact.get('job_title', '')
        contact_email = contact.get('email', '')

        replacements = {
            '{{contact_name}}': str(contact_name),
            '{{contact_first_name}}': str(contact_first_name),
            '{{contact_title}}': str(contact_title),
            '{{sender_name}}': "Tom Palmer",
            '{{sender_title}}': "Sales Manager",
            '{{today_date}}': datetime.now().strftime('%Y-%m-%d')
        }

        for placeholder, value in replacements.items():
            body = body.replace(placeholder, value)

        # Fetch and attach the email signature
        email_signature = _get_default_signature()
        if email_signature:
            signature_html = email_signature['signature_html']
            body += signature_html

        attachments = build_graph_inline_attachments()

        # Try HubSpot operations
        hubspot_company_id = None
        hubspot_contact_id = None
        try:
            if customer:
                hubspot_company_id = get_or_create_hubspot_company(customer)
            hubspot_contact_id = get_or_create_hubspot_contact(contact, customer)
        except Exception as e:
            print(f"Warning: HubSpot contact/company creation failed: {str(e)}")

        # Send the email
        try:
            result = send_graph_email(
                subject=subject,
                html_body=body.strip(),
                to_emails=[contact_email],
                bcc_emails=[bcc_email] if bcc_email else None,
                attachments=attachments,
            )
            if not result.get("success"):
                raise RuntimeError(result.get("error", "Graph send failed"))

            # Try to log to HubSpot if we have IDs
            if hubspot_contact_id:
                try:
                    hubspot_activity_id = log_email_to_hubspot(
                        hubspot_contact_id,
                        hubspot_company_id,
                        subject,
                        body,
                        contact['email']
                    )
                except Exception as e:
                    print(f"Warning: Failed to log email to HubSpot: {str(e)}")

            # Log successful send to database
            log_data = {
                'template_id': template_id,
                'contact_id': contact_id,
                'customer_id': customer_id if customer else None,
                'subject': subject,
                'recipient_email': contact_email,
                'status': 'sent'
            }
            save_email_log(log_data)

            return jsonify({
                'success': True,
                'message': f'Email sent successfully to {contact_email}'
            })

        except Exception as e:
            error_msg = f'Graph Error: {str(e)}'
            log_data = {
                'template_id': template_id,
                'contact_id': contact_id,
                'customer_id': customer_id if customer else None,
                'subject': subject,
                'recipient_email': contact_email,
                'status': 'error',
                'error_message': error_msg
            }
            save_email_log(log_data)
            return jsonify({'success': False, 'error': error_msg})

    except Exception as e:
        error_msg = f'Unexpected error: {str(e)}'
        try:
            log_data = {
                'template_id': template_id,
                'contact_id': contact_id if 'contact_id' in locals() else None,
                'customer_id': customer_id if 'customer_id' in locals() else None,
                'subject': subject if 'subject' in locals() else 'Error occurred before subject creation',
                'recipient_email': contact_email if 'contact_email' in locals() else 'Unknown',
                'status': 'error',
                'error_message': error_msg
            }
            save_email_log(log_data)
        except:
            print(f"Critical error - couldn't log error: {error_msg}")

        return jsonify({'success': False, 'error': error_msg})

def log_email_sent(template_id, contact_id, customer_id=None, subject=None, recipient_email=None, error=None):
    """
    Log the email sending attempt
    """
    try:
        log_data = {
            'template_id': template_id,
            'contact_id': contact_id,
            'customer_id': customer_id,
            'subject': subject,
            'recipient_email': recipient_email,
            'status': 'error' if error else 'sent',
            'error_message': str(error) if error else None
        }

        log_id = save_email_log(log_data)
        return log_id
    except Exception as e:
        print(f"Error logging email: {str(e)}")
        return None


def handle_db_error(f):
    """Decorator to handle database errors"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logging.error(f"Database error in {f.__name__}: {str(e)}")
            return jsonify({'error': 'Database error occurred'}), 500

    return decorated_function


@emails_bp.route('/customers', methods=['GET'])
def get_customers():
    """Get list of customers for the dropdown"""
    try:
        customers = db_execute('SELECT id, name FROM customers ORDER BY name', fetch='all')
        customer_list = [dict(row) for row in customers]

        current_app.logger.info(f"Loaded {len(customer_list)} customers")
        if customer_list:
            sample = customer_list[:5]
            current_app.logger.info(f"Sample customers: {sample}")

        return jsonify(customer_list)
    except Exception as e:
        current_app.logger.error(f"Error loading customers: {str(e)}")
        return jsonify({'error': 'Failed to load customers'}), 500


@emails_bp.route('/email-contacts')
def scan_email_contacts():
    """Render the email scanning interface."""
    return render_template('email_contacts.html')


@emails_bp.route('/api/scan-contacts')
def scan_contacts():
    app = current_app._get_current_object()

    def generate():
        with app.app_context():
            try:
                app.logger.info("Starting email scan")
                yield "data: {\"status\": \"scanning\"}\n\n"

                # Pre-fetch all ignored domains and contact data at once
                ignored_rows = db_execute('SELECT domain FROM ignored_domains', fetch='all')
                ignored_domains = {row['domain'] for row in ignored_rows or []}
                app.logger.info(f"Loaded {len(ignored_domains)} ignored domains")

                existing_rows = db_execute('''
                    SELECT contacts.id, contacts.email, contacts.name, customers.name as customer_name 
                    FROM contacts 
                    LEFT JOIN customers ON contacts.customer_id = customers.id
                ''', fetch='all')
                existing_contacts_cache = {
                    row['email']: {
                        'contact_id': row['id'],
                        'customer_name': row['customer_name'],
                        'type': 'customer',
                        'name': row['name']
                    }
                    for row in existing_rows or []
                }
                app.logger.info(f"Loaded {len(existing_contacts_cache)} existing contacts")

                supplier_rows = db_execute('''
                    SELECT 
                        sc.id, 
                        sc.email_address,
                        sc.first_name,
                        sc.second_name,
                        s.name as supplier_name
                    FROM supplier_contacts sc
                    LEFT JOIN suppliers s ON COALESCE(sc.supplier_id, sc.customer_id) = s.id
                ''', fetch='all')
                supplier_contacts_cache = {
                    row['email_address']: {
                        'contact_id': row['id'],
                        'supplier_name': row['supplier_name'],
                        'type': 'supplier',
                        'name': f"{row['first_name']} {row['second_name']}".strip()
                    }
                    for row in supplier_rows or []
                }
                app.logger.info(f"Loaded {len(supplier_contacts_cache)} supplier contacts")

                with imaplib.IMAP4_SSL(
                        os.getenv('EMAIL_HOST'),
                        int(os.getenv('EMAIL_PORT', 993))
                ) as mail:
                    mail.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_PASSWORD'))

                    # Only process INBOX
                    status, _ = mail.select("INBOX")
                    if status != "OK":
                        raise Exception("Could not select INBOX")

                    # Use more efficient UID SEARCH with batching
                    last_uid = "1"
                    search_query = f'(UID {last_uid}:*)'
                    app.logger.info(f"Executing search query: {search_query}")
                    status, messages = mail.uid('search', None, search_query)
                    app.logger.info(f"Search status: {status}, messages: {messages}")

                    if status != "OK" or not messages or messages[0] == b'':
                        app.logger.info("No messages found in search")
                        yield "data: {\"status\": \"No new emails\"}\n\n"
                        return

                    email_ids = messages[0].split()
                    app.logger.info(f"Found {len(email_ids)} email IDs to process")
                    BATCH_SIZE = 100  # Process emails in batches

                    new_contacts = {}
                    contact_updates = {}  # Track email counts for existing contacts

                    # Process emails in batches
                    for i in range(0, len(email_ids), BATCH_SIZE):
                        batch = email_ids[i:i + BATCH_SIZE]

                        # Fetch multiple emails at once
                        for email_id in batch:
                            email_id_str = email_id.decode()
                            app.logger.info(f"Processing email ID: {email_id_str}")
                            status, msg_data = mail.uid('fetch', email_id_str,
                                                        "(BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
                            app.logger.info(f"Fetch status: {status}")

                            if not msg_data or msg_data[0] is None:
                                app.logger.warning(f"No message data for email ID: {email_id_str}")
                                continue

                            try:
                                email_headers = BytesParser().parsebytes(msg_data[0][1])
                                app.logger.info(f"From header: {email_headers.get('From', 'No From header')}")

                                # Extract and process sender
                                from_header = email_headers.get("From", "").strip()
                                if not from_header:
                                    continue

                                sender_email = extract_email(from_header)
                                if not sender_email:
                                    app.logger.warning(f"Could not extract email from: {from_header}")
                                    continue

                                domain = sender_email.split('@')[-1].lower()
                                if domain in ignored_domains:
                                    app.logger.info(f"Skipping ignored domain: {domain}")
                                    continue

                                # Log contact status
                                app.logger.info(f"Processing sender: {sender_email}")
                                if sender_email in contact_updates:
                                    app.logger.info(f"Updating count for existing contact: {sender_email}")
                                    contact_updates[sender_email] += 1
                                elif sender_email in existing_contacts_cache:
                                    app.logger.info(f"Found in existing contacts: {sender_email}")
                                    contact_data = existing_contacts_cache[sender_email].copy()
                                    contact_data['email_count'] = 1
                                    contact_updates[sender_email] = 1
                                elif sender_email in supplier_contacts_cache:
                                    app.logger.info(f"Found in supplier contacts: {sender_email}")
                                    contact_data = supplier_contacts_cache[sender_email].copy()
                                    contact_data['email_count'] = 1
                                    contact_updates[sender_email] = 1
                                elif sender_email not in new_contacts:
                                    app.logger.info(f"Adding new contact: {sender_email}")
                                    name = extract_name(from_header)
                                    new_contacts[sender_email] = {
                                        'email': sender_email,
                                        'domain': domain,
                                        'name': name,
                                        'email_count': 1,
                                        'type': 'new',
                                        'latest_email': {
                                            'subject': email_headers.get('Subject', '(No subject)'),
                                            'date': email_headers.get('Date', ''),
                                            'folder': 'INBOX'
                                        }
                                    }

                                progress_payload = json.dumps({
                                    "status": "processing",
                                    "email": sender_email,
                                    "folder": "INBOX",
                                })
                                yield f"data: {progress_payload}\n\n"

                            except Exception as e:
                                app.logger.error(f"Error parsing email headers for ID {email_id_str}: {str(e)}")
                                continue

                    customer_domain_rows = db_execute(
                        'SELECT customers.name, customer_domains.domain FROM customer_domains JOIN customers ON customer_domains.customer_id = customers.id',
                        fetch='all',
                    )
                    customer_domains = {
                        row['domain']: row['name']
                        for row in customer_domain_rows or []
                    }

                    supplier_domain_rows = db_execute(
                        'SELECT suppliers.name, supplier_domains.domain FROM supplier_domains JOIN suppliers ON supplier_domains.supplier_id = suppliers.id',
                        fetch='all',
                    )
                    supplier_domains = {
                        row['domain']: row['name']
                        for row in supplier_domain_rows or []
                    }

                    existing_contacts = [
                        {**existing_contacts_cache[email], 'email_count': count, 'email': email}
                        for email, count in contact_updates.items()
                        if email in existing_contacts_cache and 'email' in existing_contacts_cache[email]
                    ]

                    existing_supplier_contacts = [
                        {**supplier_contacts_cache[email], 'email_count': count, 'email': email}
                        for email, count in contact_updates.items()
                        if email in supplier_contacts_cache and 'email' in supplier_contacts_cache[email]
                    ]

                    # Combine customer and supplier domain mappings
                    domain_to_company = {**customer_domains, **supplier_domains}

                    # Add company suggestions to new contacts
                    for contact in new_contacts.values():
                        domain = contact['domain']
                        if domain in domain_to_company:
                            contact['company_suggestions'] = [domain_to_company[domain]]

                    # Add company suggestions to existing contacts
                    for contact in existing_contacts:
                        domain = contact['email'].split('@')[-1]
                        if domain in domain_to_company:
                            contact['company_suggestions'] = [domain_to_company[domain]]

                    # Add company suggestions to existing supplier contacts
                    for contact in existing_supplier_contacts:
                        domain = contact['email'].split('@')[-1]
                        if domain in domain_to_company:
                            contact['company_suggestions'] = [domain_to_company[domain]]

                    # Prepare final results
                    app.logger.info(f"Processing complete. Found:")
                    app.logger.info(f"- {len(new_contacts)} new contacts")
                    app.logger.info(
                        f"- {sum(1 for email in contact_updates if email in existing_contacts_cache)} updated existing contacts")
                    app.logger.info(
                        f"- {sum(1 for email in contact_updates if email in supplier_contacts_cache)} updated supplier contacts")

                    # Update existing contact counts
                    existing_contacts = [
                        {**existing_contacts_cache[email], 'email_count': count}
                        for email, count in contact_updates.items()
                        if email in existing_contacts_cache
                    ]

                    existing_supplier_contacts = [
                        {**supplier_contacts_cache[email], 'email_count': count}
                        for email, count in contact_updates.items()
                        if email in supplier_contacts_cache
                    ]

                    final_result = {
                        'status': 'completed',
                        'new_contacts': list(new_contacts.values()),
                        'existing_contacts': existing_contacts,
                        'existing_supplier_contacts': existing_supplier_contacts
                    }

                    app.logger.info("Sending final results")
                    yield f"data: {json.dumps(final_result)}\n\n"

            except Exception as e:
                app.logger.error(f"Scan error: {str(e)}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream')


def extract_email(header):
    """Extract email address from header string."""
    if '<' in header and '>' in header:
        return header.split('<')[1].split('>')[0].strip()
    return header.strip()


def extract_name(header):
    """Extract name from header string."""
    if '<' in header:
        return header.split('<')[0].strip()
    return header

@emails_bp.route('/api/ignore-domain', methods=['POST'])
def ignore_domain():
    data = request.json
    domain = data.get('domain')
    reason = data.get('reason', '')

    if not domain:
        return jsonify({'error': 'Domain is required'}), 400

    try:
        db_execute(
            'INSERT INTO ignored_domains (domain, reason, created_by) VALUES (?, ?, ?)',
            (domain.lower(), reason, 'user'),
            commit=True,
        )
        return jsonify({'message': f'Domain {domain} has been ignored'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@emails_bp.route('/api/get-latest-email', methods=['GET'])
def get_latest_email():
    email = request.args.get('email')
    if not email:
        return jsonify({'error': 'Email is required'}), 400

    try:
        mail = imaplib.IMAP4_SSL(os.getenv('EMAIL_HOST'), int(os.getenv('EMAIL_PORT', 993)))
        mail.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_PASSWORD'))
        mail.select("inbox")

        # Search for latest email from this address
        _, messages = mail.search(None, f'FROM "{email}"')
        email_ids = messages[0].split()

        if not email_ids:
            return jsonify({'error': 'No emails found'}), 404

        # Get the latest email
        latest_id = email_ids[-1]
        _, msg_data = mail.fetch(latest_id, "(RFC822)")
        email_body = msg_data[0][1]
        email_msg = BytesParser().parsebytes(email_body)

        # Get plain text or HTML content
        body = ""
        if email_msg.is_multipart():
            for part in email_msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode()
                    break
                elif part.get_content_type() == "text/html":
                    body = part.get_payload(decode=True).decode()
                    break
        else:
            body = email_msg.get_payload(decode=True).decode()

        mail.logout()

        return jsonify({
            'subject': email_msg.get('Subject', '(No subject)'),
            'date': email_msg.get('Date', ''),
            'body': body
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@emails_bp.route('/contacts/add', methods=['POST'])
def add_contact():
    data = request.json
    email = data.get('email')
    name = data.get('name')
    customer_id = data.get('customer_id')
    job_title = data.get('job_title')

    if not email:
        return jsonify({'error': 'Email is required'}), 400

    try:
        with db_cursor(commit=True) as cursor:
            existing = _execute_with_cursor(
                cursor,
                'SELECT id FROM contacts WHERE email = ?',
                (email,),
                fetch='one',
            )
            if existing:
                return jsonify({'error': 'Contact already exists'}), 400

            inserted = _execute_with_cursor(
                cursor,
                'INSERT INTO contacts (customer_id, name, email, job_title) VALUES (?, ?, ?, ?) RETURNING id',
                (customer_id, name, email, job_title),
                fetch='one',
            )

            contact_id = inserted['id'] if inserted else None

        return jsonify({
            'message': 'Contact added successfully',
            'contact_id': contact_id
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def process_email_batch(mail, batch, contacts_cache, callback):
    """Process a batch of emails and yield contact data"""
    for email_id in batch:
        try:
            status, msg = mail.fetch(email_id, "(RFC822)")
            if status != 'OK':
                continue

            email_msg = None
            for response_part in msg:
                if isinstance(response_part, tuple):
                    email_msg = BytesParser().parsebytes(response_part[1])
                    break

            if not email_msg:
                continue

            sender = email_msg.get("From", "")
            if not sender:
                continue

            sender_name, sender_email = parse_sender(sender)
            if not sender_email:
                continue

            # Check cache first
            if sender_email not in contacts_cache:
                contact = db_execute(
                    '''SELECT c.id, c.name, c.email, cu.id as customer_id, cu.name as customer_name 
                       FROM contacts c 
                       LEFT JOIN customers cu ON c.customer_id = cu.id 
                       WHERE c.email = ?''',
                    (sender_email,),
                    fetch='one',
                )
                contacts_cache[sender_email] = {
                    'contact': dict(contact) if contact else None,
                    'count': 1
                }
            else:
                contacts_cache[sender_email]['count'] += 1

            cache_entry = contacts_cache[sender_email]
            contact_data = {
                'email': sender_email,
                'name': sender_name or cache_entry['contact']['name'] if cache_entry['contact'] else 'Unknown',
                'email_count': cache_entry['count'],
                'exists_in_db': cache_entry['contact'] is not None,
                'contact_id': cache_entry['contact']['id'] if cache_entry['contact'] else None,
                'customer_id': cache_entry['contact']['customer_id'] if cache_entry['contact'] else None,
                'customer_name': cache_entry['contact']['customer_name'] if cache_entry['contact'] else 'Unknown'
            }

            callback(contact_data)

        except Exception as e:
            logging.error(f"Error processing email {email_id}: {str(e)}")
            continue


def parse_sender(sender):
    """Parse sender string into name and email components"""
    try:
        if '<' in sender:
            parts = sender.split('<')
            sender_name = parts[0].strip().strip('"')
            sender_email = parts[1].replace('>', '').strip()
        else:
            sender_email = sender.strip()
            sender_name = sender_email

        # Decode sender name if encoded
        if any(encoding in sender_name.lower() for encoding in ["=?iso", "=?utf"]):
            decoded_parts = decode_header(sender_name)
            if decoded_parts and decoded_parts[0]:
                sender_name = decoded_parts[0][0]
                if isinstance(sender_name, bytes):
                    sender_name = sender_name.decode('utf-8', errors='replace')

        return sender_name, sender_email
    except Exception as e:
        logging.error(f"Error parsing sender '{sender}': {str(e)}")
        return None, None


def generate_contact_response(contact_data):
    """Generate SSE response for a contact"""
    return f"data: {json.dumps(contact_data)}\n\n"

import imaplib


def sync_new_emails():
    """
    Synchronize all emails from IMAP folders with the database.
    Processes all emails and links to contacts when possible.
    Uses Message-ID header instead of UID for unique identification.
    """
    email_host = os.getenv('EMAIL_HOST')
    email_port = int(os.getenv('EMAIL_PORT', 993))
    email_user = os.getenv('EMAIL_USER')
    email_password = os.getenv('EMAIL_PASSWORD')

    mail = imaplib.IMAP4_SSL(email_host, email_port)
    mail.login(email_user, email_password)

    contacts = get_contacts()
    # Create a case-insensitive map of contact emails
    contact_emails = {contact['email'].lower(): contact for contact in contacts}

    last_synced_row = db_execute('SELECT last_synced_date FROM sync_metadata WHERE id = 1', fetch='one')
    last_synced_date = last_synced_row['last_synced_date'] if last_synced_row else None
    current_app.logger.info(f"Last synced date from database: {last_synced_date}")

    if last_synced_date:
        try:
            parsed_date = datetime.strptime(last_synced_date, "%Y-%m-%d")
            last_synced_date_filter = f'SINCE {parsed_date.strftime("%d-%b-%Y")}'
        except ValueError as e:
            current_app.logger.error(f"Invalid last_synced_date format: {last_synced_date} - {e}")
            last_synced_date_filter = 'ALL'
    else:
        last_synced_date_filter = 'ALL'

    synced_count = 0
    try:
        status, folders = mail.list()
        current_app.logger.info(f"Raw folders: {[f.decode() for f in folders]}")
        if status != "OK":
            current_app.logger.error("Failed to retrieve folders.")
            return

        for folder in folders:
            try:
                folder_decoded = folder.decode()
                current_app.logger.info(f"Processing folder: {folder_decoded}")

                if 'INBOX.' in folder_decoded:
                    # Extract the full folder name after "." character
                    folder_name = folder_decoded.split(' "." ')[-1].strip('"')
                    current_app.logger.info(f"Found INBOX subfolder: {folder_name}")
                elif ' INBOX' in folder_decoded:
                    folder_name = 'INBOX'
                    current_app.logger.info(f"Found main INBOX: {folder_name}")
                else:
                    current_app.logger.info(f"Skipping non-INBOX folder")
                    continue

                current_app.logger.info(f"Attempting to select folder: {folder_name}")
                status, _ = mail.select(folder_name, readonly=True)
                if status != "OK":
                    current_app.logger.warning(f"Could not select folder: {folder_name}")
                    continue
                current_app.logger.info(f"Successfully selected folder: {folder_name}")

                status, messages = mail.search(None, last_synced_date_filter)
                if status != "OK":
                    current_app.logger.warning(f"No emails found in folder: {folder_name}")
                    continue

                email_ids = messages[0].split()

                for email_id in email_ids:
                    try:
                        res, msg = mail.fetch(email_id, "(RFC822)")
                        for response_part in msg:
                            if isinstance(response_part, tuple):
                                email_message = email.message_from_bytes(response_part[1])

                                message_id = email_message.get("Message-ID")
                                if not message_id:
                                    current_app.logger.warning(
                                        f"Email in folder {folder_name} is missing Message-ID header, skipping.")
                                    continue

                                message_id = clean_message_id(message_id)
                                if not message_id:
                                    current_app.logger.warning(
                                        f"Invalid Message-ID format in folder {folder_name}, skipping.")
                                    continue

                                existing_email = db_execute(
                                    'SELECT id FROM emails WHERE message_id = ?',
                                    (message_id,),
                                    fetch='one',
                                )
                                if existing_email:
                                    continue

                                subject, encoding = decode_header(email_message["Subject"])[0]
                                subject = subject.decode(encoding or 'utf-8') if isinstance(subject, bytes) else subject
                                sender = email_message.get("From")
                                raw_date = email_message.get("Date")

                                if raw_date is None:
                                    current_app.logger.warning(
                                        f"Missing date for Message-ID {message_id}, using current timestamp.")
                                    sent_date = datetime.now()
                                else:
                                    try:
                                        cleaned_date = clean_date_string(raw_date)
                                        sent_date = parser.parse(cleaned_date)
                                    except Exception as e:
                                        current_app.logger.error(
                                            f"Failed to parse date for Message-ID {message_id}: {e}, using current timestamp.")
                                        sent_date = datetime.now()

                                sender_email = sender.split('<')[-1].replace('>', '').strip().lower()

                                raw_recipients = email_message.get_all('To', [])
                                recipient_emails = []
                                for raw in raw_recipients:
                                    recipient_emails.extend(extract_emails(raw))
                                normalized_recipients = ','.join(recipient_emails)

                                your_email = email_user.lower()
                                direction = 'sent' if sender_email == your_email else 'received'

                                contact_id = None
                                customer_id = None

                                if direction == 'received':
                                    contact = contact_emails.get(sender_email)
                                    if contact:
                                        contact_id = contact['id']
                                        customer_id = contact['customer_id']
                                else:
                                    for recipient in recipient_emails:
                                        recipient = recipient.lower()
                                        contact = contact_emails.get(recipient)
                                        if contact:
                                            contact_id = contact['id']
                                            customer_id = contact['customer_id']
                                            break

                                db_execute(
                                    '''
                                    INSERT INTO emails (message_id, customer_id, contact_id, sender_email, recipient_email, 
                                                       subject, sent_date, direction, sync_status, folder)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'synced', ?)
                                    ''',
                                    (
                                        message_id, customer_id, contact_id, sender_email, normalized_recipients,
                                        subject, sent_date, direction, folder_name
                                    ),
                                    commit=True,
                                )
                                synced_count += 1
                    except Exception as e:
                        current_app.logger.error(f"Error syncing email ID {email_id} in folder {folder_name}: {e}")
                        continue
            except Exception as e:
                current_app.logger.error(f"Error processing folder {folder.decode()}: {e}")
                continue

    finally:
        now = datetime.now().strftime("%Y-%m-%d")
        metadata_row = db_execute('SELECT id FROM sync_metadata WHERE id = 1', fetch='one')
        if metadata_row:
            db_execute(
                'UPDATE sync_metadata SET last_synced_date = ? WHERE id = ?',
                (now, 1),
                commit=True,
            )
        else:
            db_execute(
                'INSERT INTO sync_metadata (id, last_synced_date) VALUES (1, ?)',
                (now,),
                commit=True,
            )
        mail.logout()

    current_app.logger.info(f"Email sync complete. Synced {synced_count} emails.")


def force_sync_historic_emails():
    """
    Force a historic sync of all emails using Message-ID to prevent duplicates.
    """
    email_host = os.getenv('EMAIL_HOST')
    email_port = int(os.getenv('EMAIL_PORT', 993))
    email_user = os.getenv('EMAIL_USER')
    email_password = os.getenv('EMAIL_PASSWORD')

    mail = imaplib.IMAP4_SSL(email_host, email_port)
    mail.login(email_user, email_password)

    historic_count = 0

    try:
        status, folders = mail.list()
        if status != "OK":
            return historic_count

        for folder in folders:
            try:
                folder_decoded = folder.decode()
                if 'INBOX.' in folder_decoded:
                    folder_name = folder_decoded.split(' "." ')[-1].strip('"')
                elif ' INBOX' in folder_decoded:
                    folder_name = 'INBOX'
                else:
                    continue

                status, _ = mail.select(folder_name, readonly=True)
                if status != "OK":
                    print(f"Could not select folder: {folder_name}")
                    continue

                status, messages = mail.search(None, 'ALL')
                if status != "OK":
                    continue

                email_ids = messages[0].split()

                for email_id in email_ids:
                    try:
                        status, msg_data = mail.fetch(email_id, "(RFC822.HEADER)")
                        if status != "OK" or not msg_data or not msg_data[0]:
                            continue

                        header_data = email.message_from_bytes(msg_data[0][1])
                        msg_id = header_data.get("Message-ID")
                        if not msg_id:
                            print(f"No Message-ID found for email {email_id}")
                            continue

                        raw_msg_id = msg_data[0][1].decode()
                        msg_id = clean_message_id(raw_msg_id)
                        if not msg_id:
                            print(f"Invalid or missing Message-ID format, skipping. Raw data: {raw_msg_id}")
                            continue

                        existing_email = db_execute(
                            "SELECT id FROM emails WHERE message_id = ?",
                            (msg_id,),
                            fetch='one',
                        )
                        if existing_email:
                            continue

                        res, msg_data = mail.fetch(email_id, "(RFC822)")
                        for response_part in msg_data:
                            if isinstance(response_part, tuple):
                                msg = email.message_from_bytes(response_part[1])
                                subject, encoding = decode_header(msg.get("Subject"))[0]
                                subject = subject.decode(encoding or 'utf-8') if isinstance(subject, bytes) else subject
                                sender_email = msg.get("From").split('<')[-1].replace('>', '').strip().lower()
                                raw_date = msg.get("Date")
                                sent_date = datetime.now() if not raw_date else email.utils.parsedate_to_datetime(raw_date)

                                recipient_emails = []
                                for raw in msg.get_all('To', []):
                                    recipient_emails.extend(extract_emails(raw))
                                normalized_recipients = ','.join(recipient_emails)

                                db_execute(
                                    '''
                                    INSERT INTO emails (message_id, sender_email, recipient_email, 
                                                       subject, sent_date, sync_status, folder)
                                    VALUES (?, ?, ?, ?, ?, 'synced', ?)
                                    ''',
                                    (
                                        msg_id, sender_email, normalized_recipients, subject, sent_date, folder_name
                                    ),
                                    commit=True,
                                )
                                historic_count += 1
                    except Exception as e:
                        print(f"Error processing email in folder {folder_name}: {e}")
                        continue
            except Exception as e:
                print(f"Error processing folder {folder_decoded}: {e}")
                continue

    finally:
        mail.logout()

    return historic_count


def clean_message_id(raw_id):
    """
    Clean a Message-ID by extracting it from header data and formatting consistently.

    This handles various formats including raw headers with Message-ID: prefix.
    """
    if not raw_id:
        return None

    # Convert bytes to string if needed
    if isinstance(raw_id, bytes):
        raw_id = raw_id.decode('utf-8', errors='ignore')

    # Find the Message-ID in the header (case insensitive)
    import re
    match = re.search(r'message-id:\s*<([^>]+)>', raw_id.lower())
    if match:
        return match.group(1)

    # If there's no Message-ID prefix but there are angle brackets
    match = re.search(r'<([^>]+)>', raw_id)
    if match:
        return match.group(1)

    # If it's already clean
    if '@' in raw_id and '<' not in raw_id and '>' not in raw_id:
        return raw_id.strip()

    # Couldn't parse it properly
    return None

@emails_bp.route('/sync_emails', methods=['POST'])
def sync_emails_route():
    try:
        sync_new_emails()
        return jsonify({'status': 'success'})
    except Exception as e:
        current_app.logger.error(f"Error in sync_emails: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

def clean_date_string(date_str):
    # Remove extra timezone strings like '(GMT+08:00)' using regex
    return re.sub(r'\s*\(GMT[^\)]+\)', '', date_str)


@emails_bp.route('/force_historic_scan', methods=['POST'])
def force_historic_scan_route():
    historic_count = force_sync_historic_emails()
    return jsonify({'message': f'Processed {historic_count} historic emails'}), 200


def extract_emails(raw_emails):
    """
    Extract and normalize email addresses from various formats:
    - Plain: user@example.com
    - Angle brackets: User Name <user@example.com>
    - Quoted: 'user@example.com' <user@example.com>
    """
    import re
    if not raw_emails:
        return []

    # First try to extract emails within angle brackets
    angle_bracket_pattern = r'<([^>]+)>'
    emails = re.findall(angle_bracket_pattern, raw_emails)

    # If no angle brackets found, look for standard email pattern
    if not emails:
        email_pattern = r'[\w\.-]+@[\w\.-]+\.\w+'
        emails = re.findall(email_pattern, raw_emails)

    # Clean and normalize addresses
    normalized_emails = []
    for email in emails:
        # Remove any surrounding quotes and whitespace
        clean_email = email.strip().strip('\'"')
        normalized_emails.append(clean_email)

    return normalized_emails

@emails_bp.route('/customer/<int:customer_id>/last_email', methods=['GET'])
def get_last_email_date(customer_id):
    try:
        result = db_execute(
            '''
            SELECT MAX(e.sent_at) as last_email
            FROM emails e
            JOIN contacts c ON e.recipient_email = c.email
            WHERE c.customer_id = ?
            ''',
            (customer_id,),
            fetch='one',
        )
        last_email = result['last_email'] if result else None
        return jsonify({
            'success': True,
            'last_email': last_email.isoformat() if last_email else None
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@emails_bp.route('/customer/<int:customer_id>/emails_by', methods=['GET'])
def get_emails_by(customer_id):
    try:
        result = db_execute(
            '''
            SELECT MAX(e.sent_at) as last_email
            FROM emails e
            JOIN contacts c ON e.recipient_email = c.email
            WHERE c.customer_id = ?
            ''',
            (customer_id,),
            fetch='one',
        )
        last_email = result['last_email'] if result else None
        return jsonify({
            'success': True,
            'last_email': last_email.isoformat() if last_email else None
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@emails_bp.route('/suppliers/')
def get_suppliers():
    suppliers = db_execute('''
        SELECT id, name 
        FROM suppliers 
        ORDER BY name
        ''', fetch='all')

    suppliers_list = [{'id': row['id'], 'name': row['name']} for row in suppliers]

    return jsonify(suppliers_list)

@emails_bp.route('/emails/content/uid/<email_uid>')
def get_email_content_by_uid(email_uid):
    email_host = os.getenv('EMAIL_HOST')
    email_port = int(os.getenv('EMAIL_PORT', 993))
    email_user = os.getenv('EMAIL_USER')
    email_password = os.getenv('EMAIL_PASSWORD')

    # Example: retrieve the folder from the DB, using email_uid if needed
    # Suppose your 'emails' table has columns: 'uid' and 'folder'
    # e.g., SELECT folder FROM emails WHERE uid = ?
    row = db_execute(
        "SELECT folder FROM emails WHERE uid = ?",
        (email_uid,),
        fetch='one',
    )
    folder = row['folder'] if row else 'INBOX'

    mail = imaplib.IMAP4_SSL(email_host, email_port)
    mail.login(email_user, email_password)

    # Select the folder in which the message resides
    # If you always store messages in 'INBOX', you can just do mail.select("INBOX")
    mail.select(folder, readonly=True)

    # Now fetch by UID instead of numeric message ID
    # The UID is typically a string, but sometimes an integer—IMAP4 expects strings
    result, msg_data = mail.uid('fetch', email_uid, '(RFC822)')

    # Set up some defaults
    subject = sender = date = ""
    email_content = None

    if result == 'OK' and msg_data and len(msg_data) > 0:
        # The actual message bytes are typically in msg_data[0][1]
        raw_email = msg_data[0][1]

        msg = email.message_from_bytes(raw_email)

        # Decode headers
        subject, encoding = decode_header(msg["Subject"])[0]
        if isinstance(subject, bytes):
            subject = subject.decode(encoding if encoding else 'utf-8')

        sender = msg.get("From")
        date = msg.get("Date")

        # Get body text
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    email_content = part.get_payload(decode=True).decode(errors='replace')
                    break
        else:
            email_content = msg.get_payload(decode=True).decode(errors='replace')

    mail.logout()

    if not email_content:
        email_content = "No content found or failed to decode."

    return jsonify({
        'subject': subject,
        'sender': sender,
        'date': date,
        'content': email_content
    })

@emails_bp.route('/customers/by-domain/<domain>')
def get_customers_by_domain(domain):
    """Get customers by email domain"""
    customers = db_execute(
        'SELECT DISTINCT c.* FROM customers c '
        'JOIN customer_domains cd ON c.id = cd.customer_id '
        'WHERE cd.domain = ?',
        (domain,),
        fetch='all',
    )
    return jsonify([dict(row) for row in customers])

@emails_bp.route('/suppliers/by-domain/<domain>')
def get_suppliers_by_domain(domain):
    """Get suppliers by email domain"""
    suppliers = db_execute(
        'SELECT DISTINCT s.* FROM suppliers s '
        'JOIN supplier_domains sd ON s.id = sd.supplier_id '
        'WHERE sd.domain = ?',
        (domain,),
        fetch='all',
    )
    return jsonify([dict(row) for row in suppliers])


@emails_bp.route('/create_excess_list_from_email/<email_id>', methods=['POST'])
def create_excess_list_from_email(email_id):
    print(f"Starting excess list creation for email {email_id}")

    # Connect to email server and get the email
    email_host = os.getenv('EMAIL_HOST')
    email_port = int(os.getenv('EMAIL_PORT', 993))
    email_user = os.getenv('EMAIL_USER')
    email_password = os.getenv('EMAIL_PASSWORD')

    try:
        print("Connecting to email server")
        # Connect to email server
        mail = imaplib.IMAP4_SSL(email_host, email_port)
        mail.login(email_user, email_password)
        mail.select("inbox")

        # Fetch the specific email
        print(f"Fetching email {email_id}")
        res, msg = mail.fetch(email_id.encode(), "(RFC822)")
        email_message = None
        for response_part in msg:
            if isinstance(response_part, tuple):
                email_message = email.message_from_bytes(response_part[1])
                break

        if not email_message:
            print("No email found")
            flash('Email not found', 'error')
            return redirect(url_for('emails.list_emails'))

        # Get email details
        subject = decode_header(email_message["Subject"])[0][0]
        if isinstance(subject, bytes):
            subject = subject.decode()
        sender = email_message.get("From")
        sender_email = sender.split('<')[-1].replace('>', '').strip()

        print(f"Processing email from {sender_email}")

        # Get the customer information
        result = get_company_name_by_email(sender_email)
        customer_contact = result['customer_contact']

        if not customer_contact:
            print(f"No customer contact found for {sender_email}")
            flash('No customer contact found for this email address', 'error')
            return redirect(url_for('emails.list_emails'))

        print(f"Found customer contact: {customer_contact}")

        try:
            with db_cursor(commit=True) as cursor:
                customer = _execute_with_cursor(
                    cursor,
                    'SELECT * FROM customers WHERE id = ?',
                    (customer_contact['customer_id'],),
                    fetch='one',
                )

                if not customer:
                    print(f"No customer found for contact {customer_contact['id']}")
                    flash('Customer not found', 'error')
                    return redirect(url_for('emails.list_emails'))

                print(f"Found customer: {customer['name']}")

                # Extract email content
                email_content = None
                if email_message.is_multipart():
                    html_content = None
                    plain_content = None

                    for part in email_message.walk():
                        if part.get_content_type() == "text/html":
                            payload = part.get_payload(decode=True)
                            if payload:
                                try:
                                    html_content = payload.decode('utf-8')
                                except UnicodeDecodeError:
                                    try:
                                        html_content = payload.decode('iso-8859-1')
                                    except UnicodeDecodeError:
                                        html_content = payload.decode('windows-1252')
                        elif part.get_content_type() == "text/plain":
                            payload = part.get_payload(decode=True)
                            if payload:
                                try:
                                    plain_content = payload.decode('utf-8')
                                except UnicodeDecodeError:
                                    try:
                                        plain_content = payload.decode('iso-8859-1')
                                    except UnicodeDecodeError:
                                        plain_content = payload.decode('windows-1252')

                    email_content = html_content if html_content else plain_content
                else:
                    payload = email_message.get_payload(decode=True)
                    if payload:
                        try:
                            email_content = payload.decode('utf-8')
                        except UnicodeDecodeError:
                            try:
                                email_content = payload.decode('iso-8859-1')
                            except UnicodeDecodeError:
                                email_content = payload.decode('windows-1252')

                print("Creating excess stock list record")
                entered_date = date.today().isoformat()

                inserted_list = _execute_with_cursor(
                    cursor,
                    '''
                    INSERT INTO excess_stock_lists (
                        name, customer_id, contact_id, entered_date, status, upload_date
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    RETURNING id
                    ''',
                    (
                        subject or 'Email Import',
                        customer['id'],
                        customer_contact['id'],
                        entered_date,
                        'new',
                        datetime.now()
                    ),
                    fetch='one',
                )

                list_id = inserted_list['id'] if inserted_list else None
                print(f"Created excess list with ID: {list_id}")

                attachment_count = 0
                for part in email_message.walk():
                    if part.get_content_maintype() == 'multipart':
                        continue
                    if part.get('Content-Disposition') is None:
                        continue

                    filename = part.get_filename()
                    if filename:
                        filename = secure_filename(filename)
                        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)

                        with open(filepath, 'wb') as f:
                            f.write(part.get_payload(decode=True))

                        file_row = _execute_with_cursor(
                            cursor,
                            '''
                            INSERT INTO files (filename, filepath, upload_date)
                            VALUES (?, ?, ?)
                            RETURNING id
                            ''',
                            (filename, filepath, datetime.now()),
                            fetch='one',
                        )
                        file_id = file_row['id']

                        _execute_with_cursor(
                            cursor,
                            '''
                            INSERT INTO excess_stock_files (excess_stock_list_id, file_id)
                            VALUES (?, ?)
                            ''',
                            (list_id, file_id),
                        )

                        attachment_count += 1

                print(f"Processed {attachment_count} attachments")

            flash('Excess list created successfully from email', 'success')
            print(f"Redirecting to excess list edit page for list {list_id}")
            return redirect(url_for('excess.edit_excess_list', list_id=list_id))

        except Exception as e:
            print(f"Database error: {str(e)}")
            flash(f'Error creating excess list from email: {str(e)}', 'error')
            return redirect(url_for('emails.list_emails'))

    except Exception as e:
        print(f"Error creating excess list: {str(e)}")
        flash(f'Error creating excess list from email: {str(e)}', 'error')
        return redirect(url_for('emails.list_emails'))

    finally:
        if 'mail' in locals():
            mail.logout()


@emails_bp.route('/create_offer_from_email/<email_id>', methods=['POST'])
def create_offer_from_email(email_id):
    print(f"Starting offer creation for email {email_id}")

    # Connect to email server and get the email
    email_host = os.getenv('EMAIL_HOST')
    email_port = int(os.getenv('EMAIL_PORT', 993))
    email_user = os.getenv('EMAIL_USER')
    email_password = os.getenv('EMAIL_PASSWORD')

    try:
        print("Connecting to email server")
        # Connect to email server
        mail = imaplib.IMAP4_SSL(email_host, email_port)
        mail.login(email_user, email_password)
        mail.select("inbox")

        # Fetch the specific email
        print(f"Fetching email {email_id}")
        res, msg = mail.fetch(email_id.encode(), "(RFC822)")
        email_message = None
        for response_part in msg:
            if isinstance(response_part, tuple):
                email_message = email.message_from_bytes(response_part[1])
                break

        if not email_message:
            print("No email found")
            flash('Email not found', 'error')
            return redirect(url_for('emails.list_emails'))

        # Get email details
        subject = decode_header(email_message["Subject"])[0][0]
        if isinstance(subject, bytes):
            subject = subject.decode()
        sender = email_message.get("From")
        sender_email = sender.split('<')[-1].replace('>', '').strip()

        print(f"Processing email from {sender_email}")

        # Get the supplier information
        result = get_company_name_by_email(sender_email)
        supplier_contact = result['supplier_contact']

        if not supplier_contact:
            print(f"No supplier contact found for {sender_email}")
            flash('No supplier contact found for this email address', 'error')
            return redirect(url_for('emails.list_emails'))

        print(f"Found supplier contact: {supplier_contact}")

        try:
            with db_cursor(commit=True) as cursor:
                supplier = _execute_with_cursor(
                    cursor,
                    'SELECT * FROM suppliers WHERE id = ?',
                    (supplier_contact['supplier_id'],),
                    fetch='one',
                )

                if not supplier:
                    print(f"No supplier found for contact {supplier_contact['id']}")
                    flash('Supplier not found', 'error')
                    return redirect(url_for('emails.list_emails'))

                print(f"Found supplier: {supplier['name']}")

                email_content = None
                if email_message.is_multipart():
                    html_content = None
                    plain_content = None

                    for part in email_message.walk():
                        if part.get_content_type() == "text/html":
                            payload = part.get_payload(decode=True)
                            if payload:
                                try:
                                    html_content = payload.decode('utf-8')
                                except UnicodeDecodeError:
                                    try:
                                        html_content = payload.decode('iso-8859-1')
                                    except UnicodeDecodeError:
                                        html_content = payload.decode('windows-1252')
                        elif part.get_content_type() == "text/plain":
                            payload = part.get_payload(decode=True)
                            if payload:
                                try:
                                    plain_content = payload.decode('utf-8')
                                except UnicodeDecodeError:
                                    try:
                                        plain_content = payload.decode('iso-8859-1')
                                    except UnicodeDecodeError:
                                        plain_content = payload.decode('windows-1252')

                    email_content = html_content if html_content else plain_content
                else:
                    payload = email_message.get_payload(decode=True)
                    if payload:
                        try:
                            email_content = payload.decode('utf-8')
                        except UnicodeDecodeError:
                            try:
                                email_content = payload.decode('iso-8859-1')
                            except UnicodeDecodeError:
                                email_content = payload.decode('windows-1252')

                print("Creating offer record")
                inserted_offer = _execute_with_cursor(
                    cursor,
                    '''
                    INSERT INTO offers (
                        supplier_id, 
                        supplier_reference, 
                        valid_to, 
                        email_content,
                        currency_id
                    ) VALUES (?, ?, ?, ?, ?)
                    RETURNING id
                    ''',
                    (
                        supplier['id'],
                        subject,
                        (datetime.now() + timedelta(days=30)).date(),
                        email_content,
                        supplier['currency']
                    ),
                    fetch='one',
                )

                offer_id = inserted_offer['id'] if inserted_offer else None
                print(f"Created offer with ID: {offer_id}")

                attachment_count = 0
                for part in email_message.walk():
                    if part.get_content_maintype() == 'multipart':
                        continue
                    if part.get('Content-Disposition') is None:
                        continue

                    filename = part.get_filename()
                    if filename:
                        filename = secure_filename(filename)
                        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)

                        with open(filepath, 'wb') as f:
                            f.write(part.get_payload(decode=True))

                        file_row = _execute_with_cursor(
                            cursor,
                            '''
                            INSERT INTO files (filename, filepath, upload_date)
                            VALUES (?, ?, ?)
                            RETURNING id
                            ''',
                            (filename, filepath, datetime.now()),
                            fetch='one',
                        )
                        file_id = file_row['id']

                        _execute_with_cursor(
                            cursor,
                            '''
                            INSERT INTO offer_files (offer_id, file_id)
                            VALUES (?, ?)
                            ''',
                            (offer_id, file_id),
                        )

                        attachment_count += 1

                        file_row = _execute_with_cursor(
                            cursor,
                            '''
                            INSERT INTO files (filename, filepath, upload_date)
                            VALUES (?, ?, ?)
                            RETURNING id
                            ''',
                            (filename, filepath, datetime.now()),
                            fetch='one',
                        )
                        file_id = file_row['id']

                        _execute_with_cursor(
                            cursor,
                            '''
                            INSERT INTO offer_files (offer_id, file_id)
                            VALUES (?, ?)
                            ''',
                            (offer_id, file_id),
                        )

                        attachment_count += 1

                print(f"Processed {attachment_count} attachments")

            flash('Offer created successfully from email', 'success')
            print(f"Redirecting to offer edit page for offer {offer_id}")
            return redirect(url_for('offers.edit_offer', offer_id=offer_id))

        except Exception as e:
            print(f"Database error: {str(e)}")
            flash(f'Error creating offer from email: {str(e)}', 'error')
            return redirect(url_for('emails.list_emails'))

    except Exception as e:
        print(f"Error creating offer: {str(e)}")
        flash(f'Error creating offer from email: {str(e)}', 'error')
        return redirect(url_for('emails.list_emails'))

    finally:
        if 'mail' in locals():
            mail.logout()


@emails_bp.route('/populate-domains', methods=['POST'])
def populate_domains_route():
    try:
        from domains import populate_domains

        # Run the domain population script
        customer_count, supplier_count = populate_domains()

        return jsonify({
            'success': True,
            'message': 'Domain tables populated successfully',
            'customer_count': customer_count,
            'supplier_count': supplier_count
        })
    except Exception as e:
        current_app.logger.error(f"Error populating domains: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to populate domains'
        }), 500


@emails_bp.route('/api/preview-email', methods=['POST'])
def preview_email():
    """
    Preview email content for both direct send and Outlook integration
    """
    try:
        data = request.json
        contact_id = data.get('contact_id')
        customer_id = data.get('customer_id')
        is_custom = data.get('is_custom', False)

        if not contact_id:
            return jsonify({'success': False, 'error': 'Contact ID is required'}), 400

        # Get contact information
        contact = get_contact_by_id(contact_id)
        if not contact:
            return jsonify({'success': False, 'error': 'Contact not found'}), 404

        # Get customer information if provided
        customer = None
        if customer_id:
            customer = get_customer_by_id(customer_id)

        if is_custom:
            # Handle custom email preview
            custom_subject = data.get('custom_subject', '')
            custom_body = data.get('custom_body', '')
            placeholders = data.get('placeholders', {})

            if not custom_subject or not custom_body:
                return jsonify({'success': False, 'error': 'Subject and body are required for custom emails'}), 400

            # Replace placeholders in subject and body
            processed_subject = custom_subject
            processed_body = custom_body

            for placeholder, value in placeholders.items():
                placeholder_pattern = f'{{{{{placeholder}}}}}'
                processed_subject = processed_subject.replace(placeholder_pattern, str(value))
                processed_body = processed_body.replace(placeholder_pattern, str(value))

            return jsonify({
                'success': True,
                'data': {
                    'subject': processed_subject,
                    'body': processed_body,
                    'recipient': contact['email'],
                    'recipient_name': contact['name']
                }
            })

        else:
            # Handle template email preview
            template_id = data.get('template_id')
            if not template_id:
                return jsonify({'success': False, 'error': 'Template ID is required'}), 400

            # Get template
            template = get_template_by_id(template_id)
            if not template:
                return jsonify({'success': False, 'error': 'Template not found'}), 404

            # Process template
            subject = template['subject']
            body = template['body']

            # Replace placeholders
            if customer:
                subject = subject.replace('{{company_name}}', customer['name'])
                body = body.replace('{{company_name}}', customer['name'])

            if contact:
                body = body.replace('{{contact_name}}', contact['name'])
                body = body.replace('{{contact_first_name}}', contact['name'].split()[0] if contact['name'] else '')
                body = body.replace('{{contact_title}}', contact.get('job_title') or '')

            # Replace sender info and date
            body = body.replace('{{sender_name}}', "Tom Palmer")
            body = body.replace('{{sender_title}}', "Sales Manager")
            body = body.replace('{{today_date}}', datetime.now().strftime('%Y-%m-%d'))

            # Convert line breaks to HTML for display
            body_html = body.replace('\n', '<br>')

            # Add email signature for preview
            email_signature = _get_default_signature()
            if email_signature:
                signature_html = email_signature['signature_html']
                # Convert CID references to actual image URLs for preview
                signature_html = signature_html.replace('cid:image001',
                                                        url_for('emails.uploaded_file', filename='blimage001.jpg'))
                signature_html = signature_html.replace('cid:linkedin_icon',
                                                        url_for('emails.uploaded_file', filename='linkedin_icon.png'))
                body_html += f"<br><br>{signature_html}"

            return jsonify({
                'success': True,
                'data': {
                    'subject': subject,
                    'body': body_html,
                    'recipient': contact['email'],
                    'recipient_name': contact['name']
                }
            })

    except Exception as e:
        current_app.logger.error(f"Error previewing email: {str(e)}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500


@emails_bp.route('/api/email-templates', methods=['GET'])
def get_email_templates():
    """
    Get all available email templates
    """
    try:
        templates = get_all_templates()
        return jsonify([{
            'id': template['id'],
            'name': template['name'],
            'subject': template['subject'],
            'body': template['body']
        } for template in templates])

    except Exception as e:
        current_app.logger.error(f"Error fetching templates: {str(e)}")
        return jsonify({'error': 'Failed to fetch templates'}), 500


@emails_bp.route('/api/send-custom-email', methods=['POST'])
def send_custom_email():
    """
    Send a custom email (non-template)
    """
    try:
        data = request.json
        contact_id = data.get('contact_id')
        customer_id = data.get('customer_id')
        subject = data.get('subject')
        body = data.get('body')
        placeholders = data.get('placeholders', {})

        if not all([contact_id, subject, body]):
            return jsonify({'success': False, 'error': 'Contact ID, subject, and body are required'}), 400

        # Get contact information
        contact = get_contact_by_id(contact_id)
        if not contact:
            return jsonify({'success': False, 'error': 'Contact not found'}), 404

        # Get customer information if provided
        customer = None
        if customer_id:
            customer = get_customer_by_id(customer_id)

        # Process placeholders
        processed_subject = subject
        processed_body = body

        for placeholder, value in placeholders.items():
            placeholder_pattern = f'{{{{{placeholder}}}}}'
            processed_subject = processed_subject.replace(placeholder_pattern, str(value))
            processed_body = processed_body.replace(placeholder_pattern, str(value))

        bcc_email = "145554557@bcc.eu1.hubspot.com"

        # Convert body to HTML
        body_html = processed_body.replace('\n', '<br>')
        body_html = f"""
        <html>
            <head>
                <style>
                    p {{ margin: 0 0 1em 0; }}
                    br {{ margin-bottom: 0.5em; }}
                </style>
            </head>
            <body>
                <div style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6;">
                    {body_html}
                </div>
            </body>
        </html>
        """

        # Add email signature
        email_signature = _get_default_signature()
        if email_signature:
            signature_html = email_signature['signature_html']
            body_html += signature_html

        attachments = build_graph_inline_attachments()
        result = send_graph_email(
            subject=processed_subject,
            html_body=body_html.strip(),
            to_emails=[contact['email']],
            bcc_emails=[bcc_email] if bcc_email else None,
            attachments=attachments,
        )
        if not result.get("success"):
            return jsonify({'success': False, 'error': result.get("error", "Graph send failed")}), 500

        # Log the email
        log_data = {
            'contact_id': contact_id,
            'customer_id': customer_id,
            'subject': processed_subject,
            'recipient_email': contact['email'],
            'status': 'sent',
            'is_custom': True
        }
        save_email_log(log_data)

        return jsonify({
            'success': True,
            'message': f'Custom email sent successfully to {contact["email"]}'
        })

    except Exception as e:
        current_app.logger.error(f"Error sending custom email: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to send email'}), 500
