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
import smtplib
import re
from datetime import date, datetime
from collections import defaultdict
import json
from functools import wraps
import time
import traceback
import uuid
from datetime import datetime, timedelta  # Add this import


# Email handling imports
from email.message import EmailMessage
from email.parser import BytesParser
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
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
from hubspot_helpers import (
    get_or_create_hubspot_contact,
    get_or_create_hubspot_company,
    log_email_to_hubspot
)
from domains import populate_domains

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


def _current_graph_user_id():
    if current_user and getattr(current_user, "is_authenticated", False):
        return getattr(current_user, "id", None)
    return None


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
    params = {
        "$top": "20",
        "$select": "id,subject,from,receivedDateTime,bodyPreview,webLink,conversationId",
    }
    resp = requests.get("https://graph.microsoft.com/v1.0/me/messages", headers=headers, params=params, timeout=20)
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
    return jsonify({
        "success": True,
        "messages": messages,
    })


@emails_bp.route('/emails/graph/message/<message_id>', methods=['GET'])
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
    resp = requests.get(f"https://graph.microsoft.com/v1.0/me/messages/{message_id}", headers=headers, params=params, timeout=20)
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


@emails_bp.route('/emails/graph/message/<message_id>/inline-attachments', methods=['GET'])
def graph_message_inline_attachments(message_id):
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
        "$select": "id,name,contentId,contentType,isInline,contentBytes",
    }
    resp = requests.get(
        f"https://graph.microsoft.com/v1.0/me/messages/{message_id}/attachments",
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
        content_id = item.get("contentId")
        content_bytes = item.get("contentBytes")
        if not item.get("isInline") or not content_id or not content_bytes:
            continue
        content_type = item.get("contentType") or "application/octet-stream"
        attachments.append({
            "content_id": content_id,
            "content_id_key": _normalize_content_id(content_id),
            "data_url": f"data:{content_type};base64,{content_bytes}",
        })

    return jsonify({
        "success": True,
        "attachments": attachments,
    })

@emails_bp.route('/emails/graph/message/<message_id>/attachments', methods=['GET'])
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
    resp = requests.get(
        f"https://graph.microsoft.com/v1.0/me/messages/{message_id}/attachments",
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


@emails_bp.route('/emails/graph/message/<message_id>/attachments/<attachment_id>', methods=['GET'])
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
    resp = requests.get(
        f"https://graph.microsoft.com/v1.0/me/messages/{message_id}/attachments/{attachment_id}",
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
        # Email details
        to_address = 't.palmer@recitalia.it'
        subject = 'Test Email from Flask App'
        email_host = 'smtps.aruba.it'  # Hardcoded SMTP host
        email_port = 465  # SSL port for SMTP
        email_user = os.getenv('EMAIL_USER')
        email_password = os.getenv('EMAIL_PASSWORD')

        # Create the email
        msg = MIMEMultipart()
        msg['From'] = email_user
        msg['To'] = to_address
        msg['Subject'] = subject
        html_content = '<p>This is a test email sent from your Flask app.</p>'
        msg.attach(MIMEText(html_content, 'html'))

        # Connect to the SMTP server using SSL and send the email
        with smtplib.SMTP_SSL(email_host, email_port) as server:  # Use SMTP_SSL for SSL connection
            server.login(email_user, email_password)
            server.send_message(msg)

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

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
            email_signature = get_email_signature_by_id(1)
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
        # Email configuration
        email_host = 'smtps.aruba.it'
        email_port = 465
        email_user = os.getenv('EMAIL_USER')
        email_password = os.getenv('EMAIL_PASSWORD')
        bcc_email = "145554557@bcc.eu1.hubspot.com"
        imap_host = 'imaps.aruba.it'  # Added IMAP host

        if not all([email_user, email_password]):
            error_msg = 'Email configuration is incomplete'
            log_data = {
                'template_id': template_id,
                'status': 'error',
                'error_message': error_msg
            }
            save_email_log(log_data)
            return jsonify({'success': False, 'error': error_msg})

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

        # Create the email message
        msg = MIMEMultipart('related')
        msg['From'] = f"Tom Palmer <{email_user}>"
        msg['To'] = contact_email
        msg['Bcc'] = bcc_email
        msg['Subject'] = subject
        msg['Date'] = datetime.now().strftime('%a, %d %b %Y %H:%M:%S +0000')

        msg_alternative = MIMEMultipart('alternative')
        msg.attach(msg_alternative)

        # Fetch and attach the email signature
        email_signature = get_email_signature_by_id(1)
        if email_signature:
            signature_html = email_signature['signature_html']
            body += signature_html

        # Attach plain text and HTML versions
        text_part = MIMEText(template['body'].strip(), 'plain')
        html_part = MIMEText(body.strip(), 'html')
        msg_alternative.attach(text_part)
        msg_alternative.attach(html_part)

        # Attach images
        uploads_dir = os.path.join(current_app.root_path, 'uploads')

        # Attach logo image
        logo_path = os.path.join(uploads_dir, 'blimage001.jpg')
        if os.path.exists(logo_path):
            with open(logo_path, 'rb') as img:
                img_data = img.read()
                image = MIMEImage(img_data)
                image.add_header('Content-ID', '<image001>')
                image.add_header('Content-Disposition', 'inline')
                msg.attach(image)

        # Attach LinkedIn icon
        linkedin_path = os.path.join(uploads_dir, 'linkedin_icon.png')
        if os.path.exists(linkedin_path):
            with open(linkedin_path, 'rb') as img:
                img_data = img.read()
                image = MIMEImage(img_data)
                image.add_header('Content-ID', '<linkedin_icon>')
                image.add_header('Content-Disposition', 'inline')
                msg.attach(image)

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
            with smtplib.SMTP_SSL(email_host, email_port) as server:
                server.login(email_user, email_password)
                server.send_message(msg)

            try:
                import imaplib
                with imaplib.IMAP4_SSL(imap_host) as imap:
                    imap.login(email_user, email_password)

                    # Select the Sent folder (name might vary by email provider)
                    sent_folder = '"Sent"'  # or 'Sent Items' or '[Gmail]/Sent Mail' depending on provider
                    imap.select(sent_folder)

                    # Convert the email message to string format
                    email_str = msg.as_string().encode('utf-8')

                    # Add the email to Sent folder
                    imap.append(sent_folder, '\\Seen', imaplib.Time2Internaldate(time.time()), email_str)

            except Exception as imap_error:
                print(f"Warning: Failed to save to Sent folder: {str(imap_error)}")

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
            error_msg = f'SMTP Error: {str(e)}'
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

                                yield f"data: {json.dumps({
                                    'status': 'processing',
                                    'email': sender_email,
                                    'folder': 'INBOX'
                                })}\n\n"

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
            email_signature = get_email_signature_by_id(1)
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

        # Email configuration
        email_host = 'smtps.aruba.it'
        email_port = 465
        email_user = os.getenv('EMAIL_USER')
        email_password = os.getenv('EMAIL_PASSWORD')
        bcc_email = "145554557@bcc.eu1.hubspot.com"

        if not all([email_user, email_password]):
            return jsonify({'success': False, 'error': 'Email configuration is incomplete'}), 500

        # Create the email message
        msg = MIMEMultipart('related')
        msg['From'] = f"Tom Palmer <{email_user}>"
        msg['To'] = contact['email']
        msg['Bcc'] = bcc_email
        msg['Subject'] = processed_subject
        msg['Date'] = datetime.now().strftime('%a, %d %b %Y %H:%M:%S +0000')

        msg_alternative = MIMEMultipart('alternative')
        msg.attach(msg_alternative)

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
        email_signature = get_email_signature_by_id(1)
        if email_signature:
            signature_html = email_signature['signature_html']
            body_html += signature_html

        # Attach plain text and HTML versions
        text_part = MIMEText(processed_body.strip(), 'plain')
        html_part = MIMEText(body_html.strip(), 'html')
        msg_alternative.attach(text_part)
        msg_alternative.attach(html_part)

        # Attach signature images
        uploads_dir = os.path.join(current_app.root_path, 'uploads')

        # Attach logo image
        logo_path = os.path.join(uploads_dir, 'blimage001.jpg')
        if os.path.exists(logo_path):
            with open(logo_path, 'rb') as img:
                img_data = img.read()
                image = MIMEImage(img_data)
                image.add_header('Content-ID', '<image001>')
                image.add_header('Content-Disposition', 'inline')
                msg.attach(image)

        # Attach LinkedIn icon
        linkedin_path = os.path.join(uploads_dir, 'linkedin_icon.png')
        if os.path.exists(linkedin_path):
            with open(linkedin_path, 'rb') as img:
                img_data = img.read()
                image = MIMEImage(img_data)
                image.add_header('Content-ID', '<linkedin_icon>')
                image.add_header('Content-Disposition', 'inline')
                msg.attach(image)

        # Send the email
        with smtplib.SMTP_SSL(email_host, email_port) as server:
            server.login(email_user, email_password)
            server.send_message(msg)

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
