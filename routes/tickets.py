import base64
import json
import re
import uuid
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, abort, flash, jsonify, current_app
import requests
from db import execute as db_execute
from routes.auth import login_required, current_user
from models import Permission
from routes.portal_admin import send_email


tickets_bp = Blueprint('tickets', __name__)


@tickets_bp.before_request
def require_login():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login', next=request.url))


def _is_admin():
    try:
        return current_user.can(Permission.ADMIN)
    except Exception:
        return False


def _parse_bool(value):
    if value is None:
        return False
    return str(value).lower() in ('1', 'true', 'yes', 'on')


def _slugify(value):
    value = re.sub(r'[^a-z0-9]+', '-', (value or '').lower()).strip('-')
    return value or 'workspace'


def _get_instance_id():
    row = db_execute(
        "SELECT value FROM app_settings WHERE key = ?",
        ("tickets_instance_id",),
        fetch="one",
    )
    if row and row.get("value"):
        return row["value"]
    instance_id = str(uuid.uuid4())
    db_execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?)",
        ("tickets_instance_id", instance_id),
        commit=True,
    )
    return instance_id


def _ensure_workspace_identity(workspace):
    workspace_id = workspace["id"]
    workspace_key = workspace.get("workspace_key")
    workspace_uuid = workspace.get("workspace_uuid")

    if not workspace_key:
        base_key = _slugify(workspace.get("name"))
        candidate = base_key
        suffix = 2
        while True:
            existing = db_execute(
                "SELECT id FROM ticket_workspaces WHERE workspace_key = ? AND id != ?",
                (candidate, workspace_id),
                fetch="one",
            )
            if not existing:
                workspace_key = candidate
                break
            candidate = f"{base_key}-{suffix}"
            suffix += 1

    if not workspace_uuid:
        workspace_uuid = str(uuid.uuid4())

    db_execute(
        """
        UPDATE ticket_workspaces
        SET workspace_key = ?, workspace_uuid = ?
        WHERE id = ?
        """,
        (workspace_key, workspace_uuid, workspace_id),
        commit=True,
    )
    workspace["workspace_key"] = workspace_key
    workspace["workspace_uuid"] = workspace_uuid
    return workspace


def _encode_import_token(payload):
    encoded = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(encoded).decode('ascii').rstrip('=')


def _decode_import_token(token):
    padded = token + '=' * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode('ascii'))
        payload = json.loads(raw.decode('utf-8'))
        return payload
    except Exception:
        return None


def _register_workspace_user_link(workspace_id, hub_workspace_uuid, local_workspace_key):
    workspace = db_execute(
        """
        SELECT id, workspace_uuid, external_base_url
        FROM ticket_workspaces
        WHERE id = ?
        """,
        (workspace_id,),
        fetch="one",
    )
    if not workspace:
        return False, "Workspace not found."

    base_url = (current_app.config.get('TICKETS_BASE_URL') or request.host_url or '').strip().rstrip('/')
    if not base_url:
        return False, 'TICKETS_BASE_URL is required.'

    payload = {
        'local_workspace_uuid': hub_workspace_uuid,
        'remote_instance_id': _get_instance_id(),
        'remote_base_url': base_url,
        'remote_workspace_uuid': workspace.get('workspace_uuid'),
        'remote_workspace_key': local_workspace_key,
    }
    response, error = _external_request(workspace, 'POST', '/api/external/workspace-link-back', payload)
    if error:
        return False, error
    if response.status_code not in (200, 201):
        return False, f'External hub returned {response.status_code}.'
    return True, None


def _validate_import_payload(payload):
    required = [
        'remote_instance_id',
        'remote_base_url',
        'remote_workspace_uuid',
        'remote_workspace_key',
        'remote_workspace_name',
    ]
    missing = [key for key in required if not (payload.get(key) or '').strip()]
    return missing


def _resolve_assignee(value, workspace_id):
    if value and str(value).startswith('external:'):
        external_id = str(value).split(':', 1)[1].strip()
        if not external_id or not workspace_id:
            return None, None, None
        row = db_execute(
            """
            SELECT
                u.display_name,
                w.external_instance_id,
                w.external_workspace_uuid
            FROM ticket_workspaces w
            LEFT JOIN external_ticket_users u
                ON u.external_instance_id = w.external_instance_id
                AND u.external_workspace_uuid = w.external_workspace_uuid
                AND u.external_user_id = ?
            WHERE w.id = ?
            """,
            (external_id, int(workspace_id)),
            fetch="one",
        )
        display_name = row.get('display_name') if row else None
        return None, external_id, display_name
    if value and str(value).isdigit():
        return int(value), None, None
    return None, None, None


def _refresh_external_users(workspace):
    base_url = (workspace.get('external_base_url') or '').strip().rstrip('/')
    workspace_uuid = (workspace.get('external_workspace_uuid') or '').strip()
    instance_id = (workspace.get('external_instance_id') or '').strip()
    api_key = (current_app.config.get('TICKETS_HUB_API_KEY') or '').strip()
    if not base_url or not workspace_uuid or not instance_id or not api_key:
        return False, 'External workspace configuration is incomplete.'
    try:
        response = requests.get(
            f"{base_url}/api/external/workspaces/{workspace_uuid}/users",
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=10,
        )
    except Exception as exc:
        return False, f'Failed to reach external workspace: {exc}'
    if response.status_code != 200:
        return False, f'External workspace returned {response.status_code}.'
    try:
        data = response.json()
    except ValueError:
        return False, 'Invalid response from external workspace.'
    users = data.get('users') or []
    if not isinstance(users, list):
        return False, 'Invalid users list.'
    to_upsert = []
    for user in users:
        remote_id = str(user.get('id') or '').strip()
        display_name = (user.get('display_name') or user.get('name') or user.get('email') or '').strip()
        if not remote_id or not display_name:
            continue
        to_upsert.append((instance_id, workspace_uuid, remote_id, display_name, user.get('email')))
    if to_upsert:
        db_execute(
            """
            INSERT INTO external_ticket_users (
                external_instance_id,
                external_workspace_uuid,
                external_user_id,
                display_name,
                email,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (external_instance_id, external_workspace_uuid, external_user_id)
            DO UPDATE SET
                display_name = EXCLUDED.display_name,
                email = EXCLUDED.email,
                updated_at = CURRENT_TIMESTAMP
            """,
            to_upsert,
            many=True,
            commit=True,
        )
    return True, users


def _external_headers():
    api_key = (current_app.config.get('TICKETS_HUB_API_KEY') or '').strip()
    if not api_key:
        return None
    return {'Authorization': f'Bearer {api_key}'}


def _external_api_token():
    token = request.headers.get('Authorization', '')
    if token.lower().startswith('bearer '):
        token = token.split(' ', 1)[1].strip()
    if not token:
        token = request.headers.get('X-Api-Key', '').strip()
    return token


def _external_api_authorized():
    api_key = (current_app.config.get('TICKETS_HUB_API_KEY') or '').strip()
    token = _external_api_token()
    if not api_key or not token:
        return False
    return token == api_key


def _external_request(workspace, method, path, payload=None):
    base_url = (workspace.get('external_base_url') or '').strip().rstrip('/')
    headers = _external_headers()
    if not base_url or not headers:
        return None, 'External hub configuration is missing.'
    try:
        response = requests.request(
            method,
            f"{base_url}{path}",
            json=payload,
            headers=headers,
            timeout=10,
        )
        return response, None
    except Exception as exc:
        return None, f'Failed to reach external hub: {exc}'


def _sync_external_workspace_tickets(workspace):
    response, error = _external_request(
        workspace,
        'GET',
        f"/api/external/workspaces/{workspace.get('external_workspace_uuid')}/tickets",
    )
    if error:
        return False, error
    if response.status_code != 200:
        return False, f'External hub returned {response.status_code}.'
    try:
        data = response.json()
    except ValueError:
        return False, 'Invalid response from external hub.'
    tickets = data.get('tickets') or []
    if not isinstance(tickets, list):
        return False, 'Invalid tickets payload.'

    external_ids = [int(t['id']) for t in tickets if str(t.get('id', '')).isdigit()]
    existing_rows = db_execute(
        """
        SELECT id, external_ticket_id
        FROM tickets
        WHERE workspace_id = ? AND external_ticket_id IS NOT NULL
        """,
        (workspace['id'],),
        fetch="all",
    ) or []
    existing_map = {row['external_ticket_id']: row['id'] for row in existing_rows}

    id_map = dict(existing_map)
    created_local_ids = []

    for ticket in tickets:
        external_id = ticket.get('id')
        if not str(external_id).isdigit():
            continue
        external_id = int(external_id)
        assigned_external_id = ticket.get('assigned_user_id')
        if assigned_external_id is not None:
            assigned_external_id = str(assigned_external_id)
        assigned_name = (ticket.get('assigned_user_name') or '').strip() or None
        parent_external_id = ticket.get('parent_ticket_id')
        if parent_external_id is not None and str(parent_external_id).isdigit():
            parent_external_id = int(parent_external_id)
        else:
            parent_external_id = None

        if external_id in existing_map:
            db_execute(
                """
                UPDATE tickets
                SET title = ?,
                    description = ?,
                    status_id = ?,
                    due_date = ?,
                    is_private = ?,
                    priority = ?,
                    external_assignee_id = ?,
                    external_assignee_name = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    ticket.get('title') or '',
                    ticket.get('description') or None,
                    ticket.get('status_id'),
                    ticket.get('due_date') or None,
                    bool(ticket.get('is_private')),
                    ticket.get('priority') or 'Medium',
                    assigned_external_id,
                    assigned_name,
                    existing_map[external_id],
                ),
                commit=True,
            )
        else:
            row = db_execute(
                """
                INSERT INTO tickets (
                    title,
                    description,
                    status_id,
                    assigned_user_id,
                    external_assignee_id,
                    external_assignee_name,
                    workspace_id,
                    created_by_user_id,
                    due_date,
                    is_private,
                    priority,
                    external_ticket_id,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (
                    ticket.get('title') or '',
                    ticket.get('description') or None,
                    ticket.get('status_id'),
                    None,
                    assigned_external_id,
                    assigned_name,
                    workspace['id'],
                    current_user.id,
                    ticket.get('due_date') or None,
                    bool(ticket.get('is_private')),
                    ticket.get('priority') or 'Medium',
                    external_id,
                ),
                fetch="one",
                commit=True,
            )
            local_id = row.get('id', list(row.values())[0]) if row else None
            if local_id:
                id_map[external_id] = local_id
                created_local_ids.append(local_id)

    for ticket in tickets:
        external_id = ticket.get('id')
        if not str(external_id).isdigit():
            continue
        external_id = int(external_id)
        parent_external_id = ticket.get('parent_ticket_id')
        if parent_external_id is not None and str(parent_external_id).isdigit():
            parent_external_id = int(parent_external_id)
        else:
            parent_external_id = None
        local_id = id_map.get(external_id)
        parent_local_id = id_map.get(parent_external_id) if parent_external_id else None
        if local_id:
            db_execute(
                "UPDATE tickets SET parent_ticket_id = ? WHERE id = ?",
                (parent_local_id, local_id),
                commit=True,
            )

    if external_ids:
        placeholders = ", ".join(["?"] * len(external_ids))
        db_execute(
            f"""
            DELETE FROM tickets
            WHERE workspace_id = ?
              AND external_ticket_id IS NOT NULL
              AND external_ticket_id NOT IN ({placeholders})
            """,
            [workspace['id']] + external_ids,
            commit=True,
        )

    return True, None


def _create_external_ticket(workspace, payload):
    response, error = _external_request(workspace, 'POST', '/api/external/tickets', payload)
    if error:
        return None, error
    if response.status_code not in (200, 201):
        try:
            data = response.json()
            return None, data.get('error') or f'External hub returned {response.status_code}.'
        except ValueError:
            return None, f'External hub returned {response.status_code}.'
    try:
        data = response.json()
    except ValueError:
        return None, 'Invalid response from external hub.'
    return data.get('ticket_id'), None


def _update_external_ticket(workspace, external_ticket_id, payload):
    response, error = _external_request(
        workspace,
        'PATCH',
        f"/api/external/tickets/{external_ticket_id}",
        payload,
    )
    if error:
        return False, error
    if response.status_code != 200:
        return False, f'External hub returned {response.status_code}.'
    return True, None


def _comment_external_ticket(workspace, external_ticket_id, update_text):
    response, error = _external_request(
        workspace,
        'POST',
        f"/api/external/tickets/{external_ticket_id}/comment",
        {'update_text': update_text},
    )
    if error:
        return False, error
    if response.status_code != 200:
        return False, f'External hub returned {response.status_code}.'
    return True, None


def _move_external_ticket(workspace, external_ticket_id, status_id):
    response, error = _external_request(
        workspace,
        'POST',
        f"/api/external/tickets/{external_ticket_id}/move",
        {'status_id': status_id},
    )
    if error:
        return False, error
    if response.status_code != 200:
        return False, f'External hub returned {response.status_code}.'
    return True, None


def _wants_json():
    return (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.accept_mimetypes.best == 'application/json'
    )


def _workspace_response(success, message, status=200, logs=None, **data):
    if _wants_json():
        payload = {
            'success': success,
            'message': message,
            'logs': logs or [],
        }
        payload.update(data)
        return jsonify(payload), status
    flash(message, 'success' if success else 'error')
    return redirect(url_for('tickets.manage_workspaces'))


def _ticket_visibility_clause(user_id, is_admin):
    if is_admin:
        return "", []
    return "AND (t.is_private = FALSE OR t.created_by_user_id = ? OR t.assigned_user_id = ?)", [user_id, user_id]


def _fetch_related_emails(ticket):
    """Fetch emails related to a ticket via Graph API"""
    conversation_id = ticket.get('email_conversation_id')
    message_id = ticket.get('email_message_id')

    if not conversation_id and not message_id:
        return []

    try:
        # Import graph functions from emails module
        from routes.emails import _get_graph_settings, _load_graph_cache_for_user, _build_msal_app

        settings = _get_graph_settings(include_secret=True)
        if not settings.get("client_id") or not settings.get("client_secret"):
            return []

        user_id = ticket.get('created_by_user_id')
        cache = _load_graph_cache_for_user(user_id) if user_id else None
        app = _build_msal_app(settings, cache=cache)
        accounts = app.get_accounts()

        if not accounts:
            return []

        token = app.acquire_token_silent(settings["scopes"], account=accounts[0])
        if user_id:
            from routes.emails import _save_graph_cache_for_user
            _save_graph_cache_for_user(user_id, cache)

        if not token or "access_token" not in token:
            return []

        import requests
        headers = {"Authorization": f"Bearer {token['access_token']}"}

        # Try to get conversation emails first
        emails = []
        if conversation_id:
            # Fetch emails in the conversation
            params = {
                "$filter": f"conversationId eq '{conversation_id}'",
                "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,bodyPreview,webLink,hasAttachments,isRead",
                "$orderby": "receivedDateTime desc",
                "$top": "50"
            }

            resp = requests.get(
                "https://graph.microsoft.com/v1.0/me/messages",
                headers=headers,
                params=params,
                timeout=10,
            )

            if resp.status_code == 200:
                data = resp.json()
                emails = data.get("value", [])

        # If no conversation emails or specific message_id, try to get the original message
        if not emails and message_id:
            params = {
                "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,bodyPreview,webLink,hasAttachments,isRead"
            }

            resp = requests.get(
                f"https://graph.microsoft.com/v1.0/me/messages/{message_id}",
                headers=headers,
                params=params,
                timeout=10,
            )

            if resp.status_code == 200:
                message = resp.json()
                emails = [message]

        # Format emails for display
        formatted_emails = []
        for email in emails:
            formatted_email = {
                'id': email.get('id'),
                'subject': email.get('subject', '(No subject)'),
                'from': email.get('from', {}).get('emailAddress', {}).get('address', 'Unknown'),
                'from_name': email.get('from', {}).get('emailAddress', {}).get('name', ''),
                'received_datetime': email.get('receivedDateTime'),
                'sent_datetime': email.get('sentDateTime'),
                'body_preview': email.get('bodyPreview', ''),
                'web_link': email.get('webLink'),
                'has_attachments': email.get('hasAttachments', False),
                'is_read': email.get('isRead', False),
            }
            formatted_emails.append(formatted_email)

        return formatted_emails

    except Exception as e:
        current_app.logger.warning(f"Failed to fetch related emails for ticket {ticket.get('id')}: {e}")
        return []


def _fetch_ticket(ticket_id, user_id, is_admin):
    visibility_clause, params = _ticket_visibility_clause(user_id, is_admin)
    row = db_execute(
        f"""
        SELECT
            t.*,
            s.name AS status_name,
            s.is_closed AS status_is_closed,
            COALESCE(au.username, t.external_assignee_name) AS assigned_user_name,
            cu.username AS created_by_name,
            pt.title AS parent_title,
            pt.is_private AS parent_is_private,
            pt.created_by_user_id AS parent_created_by_user_id,
            pt.assigned_user_id AS parent_assigned_user_id,
            tw.name AS workspace_name
        FROM tickets t
        JOIN ticket_statuses s ON t.status_id = s.id
        JOIN users cu ON t.created_by_user_id = cu.id
        LEFT JOIN users au ON t.assigned_user_id = au.id
        LEFT JOIN tickets pt ON t.parent_ticket_id = pt.id
        LEFT JOIN ticket_workspaces tw ON t.workspace_id = tw.id
        WHERE t.id = ?
        {visibility_clause}
        """,
        [ticket_id] + params,
        fetch="one",
    )
    if not row:
        return None
    ticket = dict(row)
    linked = _fetch_ticket_objects([ticket_id])
    ticket["linked_objects"] = linked.get(ticket_id, [])
    # Fetch related emails if ticket was created from email
    if ticket.get('email_message_id') or ticket.get('email_conversation_id'):
        ticket["related_emails"] = _fetch_related_emails(ticket)
    else:
        ticket["related_emails"] = []
    if ticket.get('parent_ticket_id') and ticket.get('parent_is_private') and not is_admin:
        if user_id not in (ticket.get('parent_created_by_user_id'), ticket.get('parent_assigned_user_id')):
            return None
    return ticket


def _fetch_subjobs(parent_ticket_id, user_id, is_admin):
    visibility_clause, params = _ticket_visibility_clause(user_id, is_admin)
    rows = db_execute(
        f"""
        SELECT
            t.*,
            s.name AS status_name,
            s.is_closed AS status_is_closed,
            COALESCE(au.username, t.external_assignee_name) AS assigned_user_name,
            tw.name AS workspace_name
        FROM tickets t
        JOIN ticket_statuses s ON t.status_id = s.id
        LEFT JOIN users au ON t.assigned_user_id = au.id
        LEFT JOIN ticket_workspaces tw ON t.workspace_id = tw.id
        WHERE t.parent_ticket_id = ?
        {visibility_clause}
        ORDER BY t.created_at DESC
        """,
        [parent_ticket_id] + params,
        fetch="all",
    ) or []
    return [dict(row) for row in rows]


def _fetch_statuses():
    rows = db_execute(
        "SELECT id, name, is_closed FROM ticket_statuses ORDER BY sort_order, name",
        fetch="all",
    ) or []
    return [dict(row) for row in rows]


def _fetch_users():
    rows = db_execute(
        "SELECT id, username, email FROM users ORDER BY username",
        fetch="all",
    ) or []
    return [dict(row) for row in rows]


def _fetch_workspaces():
    rows = db_execute(
        """
        SELECT
            id,
            name,
            default_assignee_id,
            workspace_key,
            workspace_uuid,
            is_external,
            external_instance_id,
            external_base_url,
            external_workspace_uuid,
            external_workspace_key
        FROM ticket_workspaces
        ORDER BY name
        """,
        fetch="all",
    ) or []
    return [dict(row) for row in rows]


def _fetch_linked_filter_options(
    object_type,
    table_name,
    visibility_clause,
    parent_visibility_clause,
    closed_clause,
    mine_clause,
    workspace_clause,
    params,
):
    rows = db_execute(
        f"""
        SELECT DISTINCT x.id, x.name
        FROM ticket_objects o
        JOIN tickets t ON o.ticket_id = t.id
        JOIN {table_name} x ON o.object_id = x.id
        JOIN ticket_statuses s ON t.status_id = s.id
        LEFT JOIN tickets pt ON t.parent_ticket_id = pt.id
        WHERE o.object_type = ?
        {visibility_clause}
        {parent_visibility_clause}
        {closed_clause}
        {mine_clause}
        {workspace_clause}
        ORDER BY x.name
        """,
        [object_type] + params,
        fetch="all",
    ) or []
    return [dict(row) for row in rows]


def _fetch_ticket_objects(ticket_ids):
    if not ticket_ids:
        return {}
    placeholders = ", ".join(["?"] * len(ticket_ids))
    rows = db_execute(
        f"""
        SELECT
            linked.ticket_id,
            linked.object_type,
            linked.object_id,
            linked.object_name
        FROM (
            SELECT
                tobj.ticket_id,
                tobj.object_type,
                tobj.object_id,
                CASE
                    WHEN tobj.object_type = 'customer' THEN c.name
                    WHEN tobj.object_type = 'supplier' THEN s.name
                    ELSE NULL
                END AS object_name
            FROM ticket_objects tobj
            LEFT JOIN customers c
                ON tobj.object_type = 'customer' AND tobj.object_id = c.id
            LEFT JOIN suppliers s
                ON tobj.object_type = 'supplier' AND tobj.object_id = s.id
            WHERE tobj.ticket_id IN ({placeholders})
        ) linked
        ORDER BY
            linked.object_type,
            CASE WHEN linked.object_name IS NULL THEN 1 ELSE 0 END,
            linked.object_name,
            linked.object_id
        """,
        ticket_ids,
        fetch="all",
    ) or []
    by_ticket = {}
    for row in rows:
        by_ticket.setdefault(row["ticket_id"], []).append({
            "object_type": row["object_type"],
            "object_id": row["object_id"],
            "object_name": row["object_name"],
        })
    return by_ticket


def _parse_id_list(raw_values):
    ids = []
    seen = set()
    for value in raw_values or []:
        try:
            int_value = int(value)
        except (TypeError, ValueError):
            continue
        if int_value in seen:
            continue
        ids.append(int_value)
        seen.add(int_value)
    return ids


def _sync_ticket_objects(ticket_id, customer_ids, supplier_ids):
    desired = set()
    for cid in customer_ids:
        desired.add(("customer", cid))
    for sid in supplier_ids:
        desired.add(("supplier", sid))

    existing_rows = db_execute(
        "SELECT object_type, object_id FROM ticket_objects WHERE ticket_id = ?",
        (ticket_id,),
        fetch="all",
    ) or []
    existing = set()
    for row in existing_rows:
        try:
            existing.add((row["object_type"], int(row["object_id"])))
        except (TypeError, ValueError):
            continue

    to_remove = existing - desired
    to_add = desired - existing

    if to_remove:
        db_execute(
            """
            DELETE FROM ticket_objects
            WHERE ticket_id = ?
              AND object_type = ?
              AND object_id = ?
            """,
            [(ticket_id, obj_type, obj_id) for obj_type, obj_id in to_remove],
            many=True,
            commit=not to_add,
        )

    if to_add:
        db_execute(
            """
            INSERT INTO ticket_objects (ticket_id, object_type, object_id, created_at, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            [(ticket_id, obj_type, obj_id) for obj_type, obj_id in to_add],
            many=True,
            commit=True,
        )


def _fetch_workspaces_with_members():
    workspaces = _fetch_workspaces()
    member_rows = db_execute(
        "SELECT workspace_id, user_id FROM ticket_workspace_members",
        fetch="all",
    ) or []
    members_by_workspace = {}
    for row in member_rows:
        members_by_workspace.setdefault(row["workspace_id"], set()).add(row["user_id"])
    for workspace in workspaces:
        workspace["member_ids"] = members_by_workspace.get(workspace["id"], set())
    return workspaces


def _fetch_workspace_chips(user_id):
    rows = db_execute(
        """
        SELECT
            tw.id,
            tw.name,
            COALESCE(COUNT(t.id), 0) AS assigned_count
        FROM ticket_workspaces tw
        JOIN ticket_workspace_members twm
            ON twm.workspace_id = tw.id
            AND twm.user_id = ?
        LEFT JOIN tickets t
            ON t.workspace_id = tw.id
            AND t.assigned_user_id = ?
        GROUP BY tw.id, tw.name
        ORDER BY tw.name
        """,
        (user_id, user_id),
        fetch="all",
    ) or []
    return [dict(row) for row in rows]


def _fetch_workspace_notifications(user_id):
    rows = db_execute(
        """
        SELECT
            tw.id AS workspace_id,
            tw.name AS workspace_name,
            COALESCE(twm.notify_ticket_assignments, FALSE) AS notify_ticket_assignments,
            COALESCE(twm.notify_task_assignments, FALSE) AS notify_task_assignments,
            COALESCE(twm.notify_ticket_returns, FALSE) AS notify_ticket_returns
        FROM ticket_workspace_members twm
        JOIN ticket_workspaces tw ON tw.id = twm.workspace_id
        WHERE twm.user_id = ?
        ORDER BY tw.name
        """,
        (user_id,),
        fetch="all",
    ) or []
    return [dict(row) for row in rows]


def _get_workspace_notification_preferences(user_id, workspace_id):
    row = db_execute(
        """
        SELECT
            COALESCE(notify_ticket_assignments, FALSE) AS notify_ticket_assignments,
            COALESCE(notify_task_assignments, FALSE) AS notify_task_assignments,
            COALESCE(notify_ticket_returns, FALSE) AS notify_ticket_returns
        FROM ticket_workspace_members
        WHERE workspace_id = ? AND user_id = ?
        """,
        (workspace_id, user_id),
        fetch="one",
    ) or {}
    return {
        "tickets": bool(row.get("notify_ticket_assignments", False)),
        "tasks": bool(row.get("notify_task_assignments", False)),
        "returns": bool(row.get("notify_ticket_returns", False)),
    }


def _get_user_contact(user_id):
    row = db_execute(
        "SELECT username, email FROM users WHERE id = ?",
        (user_id,),
        fetch="one",
    )
    if not row:
        return None
    return {
        "username": row.get("username"),
        "email": (row.get("email") or "").strip(),
    }


def _fetch_workspace_name(workspace_id):
    if not workspace_id:
        return None
    row = db_execute(
        "SELECT name FROM ticket_workspaces WHERE id = ?",
        (workspace_id,),
        fetch="one",
    )
    return row.get("name") if row else None


def _notify_assignment(ticket_id, assignee_id, workspace_id, title, is_task, workspace_lookup=None):
    if not assignee_id or not workspace_id:
        return
    preferences = _get_workspace_notification_preferences(assignee_id, workspace_id)
    wants_notification = preferences["tasks"] if is_task else preferences["tickets"]
    if not wants_notification:
        return

    contact = _get_user_contact(assignee_id)
    if not contact or not contact.get("email"):
        return

    workspace_name = None
    if workspace_lookup and workspace_id in workspace_lookup:
        workspace_name = workspace_lookup[workspace_id].get("name")
    if not workspace_name:
        workspace_name = _fetch_workspace_name(workspace_id) or "workspace"

    ticket_url = url_for('tickets.view_ticket', ticket_id=ticket_id, _external=True)
    subject = f"New {'task' if is_task else 'ticket'} assigned: #{ticket_id}"
    greeting = contact.get("username") or "there"
    html_body = (
        f"<p>Hi {greeting},</p>"
        f"<p>You have been assigned a {'task' if is_task else 'ticket'} in the <strong>{workspace_name}</strong> workspace.</p>"
        f"<p><strong>#{ticket_id} {title}</strong></p>"
        f'<p><a href="{ticket_url}">View in Sproutt</a></p>'
    )
    text_body = (
        f"Hi {greeting},\n\n"
        f"You have been assigned a {'task' if is_task else 'ticket'} in the {workspace_name} workspace.\n"
        f"#{ticket_id} {title}\n\n"
        f"Open: {ticket_url}"
    )
    send_email(contact["email"], subject, html_body, text_body)


def _notify_returned_ticket(ticket_id, creator_id, workspace_id, title, workspace_lookup=None):
    """Send notification to ticket creator when ticket is returned."""
    if not creator_id or not workspace_id:
        return
    preferences = _get_workspace_notification_preferences(creator_id, workspace_id)
    if not preferences.get("returns"):
        return

    contact = _get_user_contact(creator_id)
    if not contact or not contact.get("email"):
        return

    workspace_name = None
    if workspace_lookup and workspace_id in workspace_lookup:
        workspace_name = workspace_lookup[workspace_id].get("name")
    if not workspace_name:
        workspace_name = _fetch_workspace_name(workspace_id) or "workspace"

    ticket_url = url_for('tickets.view_ticket', ticket_id=ticket_id, _external=True)
    subject = f"Ticket returned: #{ticket_id}"
    greeting = contact.get("username") or "there"
    html_body = (
        f"<p>Hi {greeting},</p>"
        f"<p>Your ticket has been returned in the <strong>{workspace_name}</strong> workspace.</p>"
        f"<p><strong>#{ticket_id} {title}</strong></p>"
        f'<p><a href="{ticket_url}">View in Sproutt</a></p>'
    )
    text_body = (
        f"Hi {greeting},\n\n"
        f"Your ticket has been returned in the {workspace_name} workspace.\n"
        f"#{ticket_id} {title}\n\n"
        f"View: {ticket_url}"
    )
    send_email(contact["email"], subject, html_body, text_body)


def _fetch_updates(ticket_id):
    rows = db_execute(
        """
        SELECT tu.id,
               tu.update_text,
               tu.created_at,
               u.username AS user_name
        FROM ticket_updates tu
        JOIN users u ON tu.user_id = u.id
        WHERE tu.ticket_id = ?
        ORDER BY tu.created_at DESC, tu.id DESC
        """,
        (ticket_id,),
        fetch="all",
    ) or []
    return [dict(row) for row in rows]


def _add_ticket_update(ticket_id, user_id, update_text):
    if not update_text:
        return
    db_execute(
        """
        INSERT INTO ticket_updates (ticket_id, user_id, update_text, created_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (ticket_id, user_id, update_text),
        commit=True,
    )


def _status_name(status_id):
    row = db_execute(
        "SELECT name FROM ticket_statuses WHERE id = ?",
        (status_id,),
        fetch="one",
    )
    return row.get('name') if row else None


def _parse_mentions(update_text):
    """Extract usernames from @[username] mentions in text."""
    if not update_text:
        return []
    # Match @[username] pattern
    pattern = r'@\[([^\]]+)\]'
    matches = re.findall(pattern, update_text)
    return list(set(matches))  # Return unique usernames


def _get_users_by_usernames(usernames):
    """Fetch user details by usernames."""
    if not usernames:
        return []
    placeholders = ", ".join(["?"] * len(usernames))
    rows = db_execute(
        f"SELECT id, username, email FROM users WHERE username IN ({placeholders})",
        usernames,
        fetch="all",
    ) or []
    return [dict(row) for row in rows]


def _notify_mentioned_users(ticket_id, ticket_title, update_text, mentioned_users, author_user_id):
    """Send email notifications to mentioned users."""
    if not mentioned_users:
        return

    # Get author info
    author = _get_user_contact(author_user_id)
    author_name = author.get('username', 'Someone') if author else 'Someone'

    ticket_url = url_for('tickets.view_ticket', ticket_id=ticket_id, _external=True)

    for user in mentioned_users:
        # Don't notify the author if they mention themselves
        if user.get('id') == author_user_id:
            continue

        email = (user.get('email') or '').strip()
        if not email:
            continue

        username = user.get('username', 'there')
        subject = f"You were mentioned in ticket #{ticket_id}"

        # Convert @[username] to @username for display
        display_text = re.sub(r'@\[([^\]]+)\]', r'@\1', update_text)

        html_body = (
            f"<p>Hi {username},</p>"
            f"<p><strong>{author_name}</strong> mentioned you in a comment on ticket "
            f"<strong>#{ticket_id} {ticket_title}</strong>:</p>"
            f"<blockquote style=\"border-left: 3px solid #1a73e8; padding-left: 12px; margin: 16px 0; color: #495057;\">"
            f"{display_text}</blockquote>"
            f'<p><a href="{ticket_url}" style="color: #1a73e8;">View ticket</a></p>'
        )
        text_body = (
            f"Hi {username},\n\n"
            f"{author_name} mentioned you in a comment on ticket #{ticket_id} {ticket_title}:\n\n"
            f"\"{display_text}\"\n\n"
            f"View ticket: {ticket_url}"
        )

        send_email(email, subject, html_body, text_body)


@tickets_bp.route('/from-email', methods=['POST'])
@login_required
def create_from_email():
    """Create a ticket from an email message."""
    user_id = current_user.id
    data = request.get_json(silent=True) or {}

    email_message_id = data.get('message_id', '').strip()
    email_conversation_id = data.get('conversation_id', '').strip()
    email_from = data.get('from_email', '').strip()
    email_subject = data.get('subject', '').strip()
    description = data.get('body_preview', '').strip()
    assigned_user_id = data.get('assigned_user_id') or None

    if not email_subject:
        return jsonify({'success': False, 'error': 'Subject is required'}), 400

    # Get default open status
    statuses = _fetch_statuses()
    default_status_id = None
    for status in statuses:
        if not status.get('is_closed'):
            default_status_id = status['id']
            break

    if not default_status_id:
        return jsonify({'success': False, 'error': 'No open status found'}), 500

    # Check if a ticket already exists for this conversation
    existing_ticket = None
    if email_conversation_id:
        existing_ticket = db_execute(
            "SELECT id, title FROM tickets WHERE email_conversation_id = ? LIMIT 1",
            (email_conversation_id,),
            fetch="one",
        )

    if existing_ticket:
        # Add an update to the existing ticket instead
        _add_ticket_update(
            existing_ticket['id'],
            user_id,
            f"New email in conversation from {email_from}: {email_subject}"
        )
        return jsonify({
            'success': True,
            'ticket_id': existing_ticket['id'],
            'existing': True,
            'message': f"Added update to existing ticket #{existing_ticket['id']}"
        })

    # Create new ticket - use simpler INSERT without email columns if they don't exist
    title = f"[Email] {email_subject}" if email_subject else "[Email] No subject"
    full_description = f"From: {email_from}\n\n{description}" if email_from else description

    try:
        row = db_execute(
            """
            INSERT INTO tickets (
                title,
                description,
                status_id,
                assigned_user_id,
                created_by_user_id,
                is_private,
                email_message_id,
                email_conversation_id,
                email_from,
                email_subject,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, FALSE, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (
                title,
                full_description or None,
                default_status_id,
                int(assigned_user_id) if assigned_user_id else user_id,
                user_id,
                email_message_id or None,
                email_conversation_id or None,
                email_from or None,
                email_subject or None,
            ),
            fetch="one",
            commit=True,
        )
    except Exception:
        # Fallback: try without email columns if migration hasn't been run
        row = db_execute(
            """
            INSERT INTO tickets (
                title,
                description,
                status_id,
                assigned_user_id,
                created_by_user_id,
                is_private,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, FALSE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (
                title,
                full_description or None,
                default_status_id,
                int(assigned_user_id) if assigned_user_id else user_id,
                user_id,
            ),
            fetch="one",
            commit=True,
        )

    ticket_id = row.get('id', list(row.values())[0]) if row else None

    if not ticket_id:
        return jsonify({'success': False, 'error': 'Failed to create ticket'}), 500

    return jsonify({
        'success': True,
        'ticket_id': ticket_id,
        'existing': False,
        'message': f"Created ticket #{ticket_id}"
    })


@tickets_bp.route('/by-conversation/<conversation_id>', methods=['GET'])
@login_required
def get_ticket_by_conversation(conversation_id):
    """Get a ticket by email conversation ID."""
    user_id = current_user.id
    is_admin = _is_admin()
    visibility_clause, params = _ticket_visibility_clause(user_id, is_admin)

    row = db_execute(
        f"""
        SELECT t.id, t.title, t.status_id, s.name AS status_name
        FROM tickets t
        JOIN ticket_statuses s ON t.status_id = s.id
        WHERE t.email_conversation_id = ?
        {visibility_clause}
        LIMIT 1
        """,
        [conversation_id] + params,
        fetch="one",
    )

    if not row:
        return jsonify({'success': True, 'ticket': None})

    return jsonify({
        'success': True,
        'ticket': {
            'id': row['id'],
            'title': row['title'],
            'status_id': row['status_id'],
            'status_name': row['status_name'],
        }
    })


@tickets_bp.route('/', methods=['GET', 'POST'])
@login_required
def list_tickets():
    user_id = current_user.id
    is_admin = _is_admin()

    if request.method == 'POST':
        workspaces = _fetch_workspaces()
        workspace_lookup = {workspace['id']: workspace for workspace in workspaces}
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        assigned_user_value = request.form.get('assigned_user_id') or None
        workspace_id = request.form.get('workspace_id') or None
        priority = request.form.get('priority', 'Medium')
        if not workspace_id:
            workspace_filter = request.args.get('workspace_id', '')
            if str(workspace_filter).isdigit():
                workspace_id = workspace_filter
        status_id = request.form.get('status_id')
        due_date = request.form.get('due_date') or None
        is_private = bool(request.form.get('is_private'))
        customer_ids = _parse_id_list(request.form.getlist('customer_ids'))
        supplier_ids = _parse_id_list(request.form.getlist('supplier_ids'))

        if not title:
            flash('Title is required.', 'error')
            return redirect(url_for('tickets.list_tickets'))

        if not status_id:
            flash('Status is required.', 'error')
            return redirect(url_for('tickets.list_tickets'))

        assigned_user_id, external_assignee_id, external_assignee_name = _resolve_assignee(
            assigned_user_value,
            workspace_id,
        )

        if not assigned_user_id and not external_assignee_id and workspace_id and str(workspace_id).isdigit():
            workspace = workspace_lookup.get(int(workspace_id))
            default_assignee_id = workspace.get('default_assignee_id') if workspace else None
            if default_assignee_id and not (workspace or {}).get('is_external'):
                assigned_user_id = int(default_assignee_id)

        external_ticket_id = None
        if workspace_id and str(workspace_id).isdigit():
            workspace = workspace_lookup.get(int(workspace_id))
        else:
            workspace = None

        if workspace and workspace.get('is_external'):
            external_payload = {
                'title': title,
                'description': description,
                'workspace_key': workspace.get('external_workspace_key') or workspace.get('workspace_key'),
                'priority': priority,
                'due_date': due_date,
                'is_private': bool(is_private),
                'assigned_user_id': external_assignee_id,
            }
            external_ticket_id, error = _create_external_ticket(workspace, external_payload)
            if error or not external_ticket_id:
                flash(error or 'Unable to create external ticket.', 'error')
                return redirect(url_for('tickets.list_tickets'))

        row = db_execute(
            """
                INSERT INTO tickets (
                    title,
                    description,
                    status_id,
                    assigned_user_id,
                    external_assignee_id,
                    external_assignee_name,
                    workspace_id,
                    created_by_user_id,
                    due_date,
                    is_private,
                    priority,
                    external_ticket_id,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id
                """,
            (
                title,
                description or None,
                int(status_id),
                int(assigned_user_id) if assigned_user_id else None,
                external_assignee_id,
                external_assignee_name,
                int(workspace_id) if workspace_id else None,
                user_id,
                due_date,
                is_private,
                priority,
                external_ticket_id,
            ),
            fetch="one",
            commit=True,
        )
        ticket_id = row.get('id', list(row.values())[0]) if row else None
        if ticket_id:
            _sync_ticket_objects(ticket_id, customer_ids, supplier_ids)
            if assigned_user_id:
                _notify_assignment(
                    ticket_id,
                    int(assigned_user_id),
                    int(workspace_id) if workspace_id else None,
                    title,
                    False,
                    workspace_lookup,
                )
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    show_closed = _parse_bool(request.args.get('show_closed'))
    only_mine = _parse_bool(request.args.get('only_mine'))
    created_by_me = _parse_bool(request.args.get('created_by_me'))
    if request.args.get('only_mine') is None and request.args.get('created_by_me') is None:
        only_mine = True
    workspace_filter_id = request.args.get('workspace_id') or None
    customer_filter_id = request.args.get('customer_id') or None
    supplier_filter_id = request.args.get('supplier_id') or None
    if customer_filter_id and not str(customer_filter_id).isdigit():
        customer_filter_id = None
    if supplier_filter_id and not str(supplier_filter_id).isdigit():
        supplier_filter_id = None

    if workspace_filter_id and str(workspace_filter_id).isdigit():
        workspace = next((w for w in _fetch_workspaces() if w['id'] == int(workspace_filter_id)), None)
        if workspace and workspace.get('is_external'):
            ok, error = _sync_external_workspace_tickets(workspace)
            if not ok:
                flash(error or 'Unable to sync external workspace.', 'error')

    visibility_clause, visibility_params = _ticket_visibility_clause(user_id, is_admin)
    closed_clause = "" if show_closed else "AND s.is_closed = FALSE"
    mine_clause = ""
    mine_params = []
    if only_mine and created_by_me:
        # Use OR when both are selected - show tickets assigned to me OR created by me
        mine_clause = "AND (t.assigned_user_id = ? OR t.created_by_user_id = ?)"
        mine_params = [user_id, user_id]
    elif only_mine:
        mine_clause = "AND t.assigned_user_id = ?"
        mine_params = [user_id]
    elif created_by_me:
        mine_clause = "AND t.created_by_user_id = ?"
        mine_params = [user_id]

    workspace_clause = ""
    workspace_params = []
    if workspace_filter_id:
        workspace_clause = "AND t.workspace_id = ?"
        workspace_params = [int(workspace_filter_id)]

    object_filter_clause = ""
    object_filter_params = []
    if customer_filter_id and str(customer_filter_id).isdigit():
        object_filter_clause += (
            "AND EXISTS (SELECT 1 FROM ticket_objects o "
            "WHERE o.ticket_id = t.id AND o.object_type = 'customer' AND o.object_id = ?)"
        )
        object_filter_params.append(int(customer_filter_id))
    if supplier_filter_id and str(supplier_filter_id).isdigit():
        object_filter_clause += (
            "AND EXISTS (SELECT 1 FROM ticket_objects o "
            "WHERE o.ticket_id = t.id AND o.object_type = 'supplier' AND o.object_id = ?)"
        )
        object_filter_params.append(int(supplier_filter_id))

    parent_visibility_clause = ""
    parent_visibility_params = []
    if not is_admin:
        parent_visibility_clause = (
            "AND (t.parent_ticket_id IS NULL "
            "OR pt.is_private = FALSE "
            "OR pt.created_by_user_id = ? "
            "OR pt.assigned_user_id = ?)"
        )
        parent_visibility_params = [user_id, user_id]

    rows = db_execute(
        f"""
          SELECT
              t.*,
              s.name AS status_name,
              s.is_closed AS status_is_closed,
              COALESCE(au.username, t.external_assignee_name) AS assigned_user_name,
              cu.username AS created_by_name,
              pt.id AS parent_id,
            pt.title AS parent_title,
            pt.parent_ticket_id AS parent_parent_id,
            tw.name AS workspace_name,
            COALESCE(sc.subjob_count, 0) AS subjob_count,
            COALESCE(sc.closed_subjob_count, 0) AS closed_subjob_count
        FROM tickets t
        JOIN ticket_statuses s ON t.status_id = s.id
        JOIN users cu ON t.created_by_user_id = cu.id
        LEFT JOIN users au ON t.assigned_user_id = au.id
        LEFT JOIN tickets pt ON t.parent_ticket_id = pt.id
        LEFT JOIN ticket_workspaces tw ON t.workspace_id = tw.id
        LEFT JOIN (
            SELECT
                t2.parent_ticket_id,
                COUNT(*) AS subjob_count,
                SUM(CASE WHEN s2.is_closed THEN 1 ELSE 0 END) AS closed_subjob_count
            FROM tickets t2
            JOIN ticket_statuses s2 ON t2.status_id = s2.id
            WHERE t2.parent_ticket_id IS NOT NULL
            GROUP BY t2.parent_ticket_id
        ) sc ON sc.parent_ticket_id = t.id
        WHERE 1 = 1
        {visibility_clause}
        {parent_visibility_clause}
        {closed_clause}
        {mine_clause}
        {workspace_clause}
        {object_filter_clause}
        ORDER BY
            CASE WHEN t.due_date IS NULL THEN 1 ELSE 0 END,
            t.due_date ASC,
            t.updated_at DESC
        """,
        visibility_params + parent_visibility_params + mine_params + workspace_params + object_filter_params,
        fetch="all",
    ) or []

    tickets = [dict(row) for row in rows]
    linked_by_ticket = _fetch_ticket_objects([ticket["id"] for ticket in tickets])
    for ticket in tickets:
        ticket["linked_objects"] = linked_by_ticket.get(ticket["id"], [])
    ticket_lookup = {ticket["id"]: ticket for ticket in tickets}
    for ticket in tickets:
        chain_titles = []
        parent_id = ticket.get("parent_id")
        hop_count = 0
        while parent_id and hop_count < 5:
            parent = ticket_lookup.get(parent_id)
            if not parent:
                break
            chain_titles.append(f"#{parent['id']} {parent['title']}")
            parent_id = parent.get("parent_id")
            hop_count += 1
        ticket["parent_chain"] = " > ".join(reversed(chain_titles)) if chain_titles else None
        ticket["depth"] = len(chain_titles)
        if ticket.get("parent_id") and ticket.get("parent_title"):
            ticket["parent_label"] = f"#{ticket['parent_id']} {ticket['parent_title']}"
        else:
            ticket["parent_label"] = None
        ticket["is_context_only"] = False
    statuses = _fetch_statuses()
    users = _fetch_users()
    workspaces = _fetch_workspaces()
    filter_params = visibility_params + parent_visibility_params + mine_params + workspace_params
    customers = _fetch_linked_filter_options(
        "customer",
        "customers",
        visibility_clause,
        parent_visibility_clause,
        closed_clause,
        mine_clause,
        workspace_clause,
        filter_params,
    )
    suppliers = _fetch_linked_filter_options(
        "supplier",
        "suppliers",
        visibility_clause,
        parent_visibility_clause,
        closed_clause,
        mine_clause,
        workspace_clause,
        filter_params,
    )
    workspace_defaults = {
        workspace['id']: workspace.get('default_assignee_id')
        for workspace in workspaces
    }
    workspace_external_map = {
        workspace['id']: bool(workspace.get('is_external'))
        for workspace in workspaces
    }
    workspace_chips = _fetch_workspace_chips(user_id)
    default_status_id = None
    for status in statuses:
        if not status.get('is_closed'):
            default_status_id = status['id']
            break

    tickets_by_status = {status['id']: [] for status in statuses}
    status_groups = {}
    for ticket in tickets:
        status_groups.setdefault(ticket['status_id'], []).append(ticket)

    for status_id, group in status_groups.items():
        group_ids = {ticket["id"] for ticket in group}
        expanded_group = list(group)
        expanded_ids = set(group_ids)

        for ticket in group:
            parent_id = ticket.get("parent_id")
            hop_count = 0
            while parent_id and hop_count < 5:
                parent = ticket_lookup.get(parent_id)
                if not parent:
                    break
                if parent_id not in expanded_ids:
                    context_ticket = dict(parent)
                    context_ticket["is_context_only"] = True
                    context_ticket["context_for_status_id"] = status_id
                    expanded_group.append(context_ticket)
                    expanded_ids.add(parent_id)
                parent_id = parent.get("parent_id")
                hop_count += 1

        group = expanded_group
        group_ids = expanded_ids
        children_by_parent = {}
        for ticket in group:
            parent_id = ticket.get("parent_id")
            if parent_id:
                children_by_parent.setdefault(parent_id, []).append(ticket)

        ordered = []
        seen = set()

        def append_children(parent_id):
            for child in children_by_parent.get(parent_id, []):
                if child["id"] in seen:
                    continue
                ordered.append(child)
                seen.add(child["id"])
                append_children(child["id"])

        for ticket in group:
            if ticket["id"] in seen:
                continue
            if ticket.get("parent_id") and ticket["parent_id"] in group_ids:
                continue
            ordered.append(ticket)
            seen.add(ticket["id"])
            append_children(ticket["id"])

        for ticket in group:
            if ticket["id"] not in seen:
                ordered.append(ticket)
                seen.add(ticket["id"])
                append_children(ticket["id"])

        tickets_by_status[status_id] = ordered

    return render_template(
        'tickets.html',
        tickets_by_status=tickets_by_status,
        statuses=statuses,
        users=users,
        workspaces=workspaces,
        workspace_chips=workspace_chips,
        show_closed=show_closed,
        only_mine=only_mine,
        created_by_me=created_by_me,
        workspace_filter_id=workspace_filter_id,
        customer_filter_id=customer_filter_id,
        supplier_filter_id=supplier_filter_id,
        default_status_id=default_status_id,
        workspace_defaults=workspace_defaults,
        workspace_external_map=workspace_external_map,
        customers=customers,
        suppliers=suppliers,
    )


@tickets_bp.route('/sidebar-tree', methods=['GET'])
@login_required
def sidebar_tree():
    user_id = current_user.id
    is_admin = _is_admin()
    visibility_clause, visibility_params = _ticket_visibility_clause(user_id, is_admin)

    statuses = _fetch_statuses()
    status_by_id = {status['id']: status for status in statuses}
    status_order = {status['id']: index for index, status in enumerate(statuses)}
    open_status_id = next(
        (status['id'] for status in statuses if status.get('name', '').lower() == 'open'),
        None,
    )
    return_status_id = next(
        (status['id'] for status in statuses if status.get('name', '').lower() == 'returned'),
        None,
    )
    close_status_id = next(
        (status['id'] for status in statuses if status.get('is_closed')),
        None,
    )

    status_ids_to_include = []
    if open_status_id and open_status_id not in status_ids_to_include:
        status_ids_to_include.append(open_status_id)
    if return_status_id and return_status_id not in status_ids_to_include:
        status_ids_to_include.append(return_status_id)

    status_filter_clause = ""
    status_filter_params = []
    if status_ids_to_include:
        placeholders = ", ".join(["?"] * len(status_ids_to_include))
        status_filter_clause = f" AND t.status_id IN ({placeholders})"
        status_filter_params.extend(status_ids_to_include)

    assigned_rows = db_execute(
        f"""
        SELECT
            t.id,
            t.title,
            t.status_id,
            t.parent_ticket_id,
            t.workspace_id,
            t.assigned_user_id,
            s.name AS status_name,
            s.is_closed AS status_is_closed,
            tw.name AS workspace_name
        FROM tickets t
        JOIN ticket_statuses s ON t.status_id = s.id
        LEFT JOIN ticket_workspaces tw ON t.workspace_id = tw.id
        WHERE t.assigned_user_id = ?
          AND s.is_closed = FALSE
          {status_filter_clause}
        {visibility_clause}
        """,
        [user_id] + status_filter_params + visibility_params,
        fetch="all",
    ) or []

    assigned_tickets = [dict(row) for row in assigned_rows]
    if not assigned_tickets:
        return jsonify({
            'success': True,
            'workspaces': [],
            'assigned_total': 0,
            'return_status_id': return_status_id,
            'close_status_id': close_status_id,
        })

    ticket_rows = {ticket['id']: ticket for ticket in assigned_tickets}
    seen_ids = set(ticket_rows.keys())
    to_fetch = {
        ticket.get('parent_ticket_id')
        for ticket in assigned_tickets
        if ticket.get('parent_ticket_id')
    }

    while to_fetch:
        batch = [ticket_id for ticket_id in to_fetch if ticket_id not in seen_ids]
        to_fetch = set()
        if not batch:
            break
        placeholders = ", ".join(["?"] * len(batch))
        parent_rows = db_execute(
            f"""
            SELECT
                t.id,
                t.title,
                t.status_id,
                t.parent_ticket_id,
                t.workspace_id,
                t.assigned_user_id,
                s.name AS status_name,
                s.is_closed AS status_is_closed,
                tw.name AS workspace_name
            FROM tickets t
            JOIN ticket_statuses s ON t.status_id = s.id
            LEFT JOIN ticket_workspaces tw ON t.workspace_id = tw.id
            WHERE t.id IN ({placeholders})
              AND s.is_closed = FALSE
              {status_filter_clause}
            {visibility_clause}
            """,
            batch + status_filter_params + visibility_params,
            fetch="all",
        ) or []
        for row in parent_rows:
            ticket = dict(row)
            if ticket['id'] in seen_ids:
                continue
            ticket_rows[ticket['id']] = ticket
            seen_ids.add(ticket['id'])
            parent_id = ticket.get('parent_ticket_id')
            if parent_id and parent_id not in seen_ids:
                to_fetch.add(parent_id)

    groups = {}
    for ticket in assigned_tickets:
        group_key = (ticket.get('workspace_id'), ticket.get('status_id'))
        group = groups.setdefault(group_key, {
            'workspace_id': ticket.get('workspace_id'),
            'workspace_name': ticket.get('workspace_name') or 'No Workspace',
            'status_id': ticket.get('status_id'),
            'nodes': {},
        })

        path = []
        current_id = ticket.get('id')
        hop_count = 0
        while current_id and hop_count < 10:
            if current_id in path:
                break
            row = ticket_rows.get(current_id)
            if not row:
                break
            path.append(current_id)
            current_id = row.get('parent_ticket_id')
            hop_count += 1

        for ticket_id in path:
            if ticket_id in group['nodes']:
                continue
            row = ticket_rows[ticket_id]
            group['nodes'][ticket_id] = {
                'id': row['id'],
                'title': row['title'],
                'parent_id': row.get('parent_ticket_id'),
                'assigned_user_id': row.get('assigned_user_id'),
                'is_assigned_to_me': row.get('assigned_user_id') == user_id,
                'is_context_only': row.get('assigned_user_id') != user_id,
                'status_id': row.get('status_id'),
                'status_name': row.get('status_name'),
            }

    workspaces = {}
    assigned_total = 0
    for (workspace_id, status_id), group in groups.items():
        status = status_by_id.get(status_id, {})
        workspace = workspaces.setdefault(workspace_id, {
            'id': workspace_id,
            'name': group.get('workspace_name') or 'No Workspace',
            'statuses': [],
        })

        nodes = group['nodes']
        for node in nodes.values():
            node['children'] = []
        for node in nodes.values():
            parent_id = node.get('parent_id')
            if parent_id in nodes:
                nodes[parent_id]['children'].append(node)

        roots = [node for node in nodes.values() if node.get('parent_id') not in nodes]

        def sort_nodes(items):
            items.sort(key=lambda item: (item.get('title') or '').lower())
            for item in items:
                sort_nodes(item.get('children', []))

        sort_nodes(roots)

        assigned_count = sum(
            1 for node in nodes.values()
            if node.get('is_assigned_to_me') and node.get('status_id') == status_id
        )
        assigned_total += assigned_count

        workspace['statuses'].append({
            'id': status_id,
            'name': status.get('name') or 'Status',
            'tickets': roots,
            'assigned_count': assigned_count,
        })

    workspace_list = list(workspaces.values())
    workspace_list.sort(key=lambda item: (item.get('name') or '').lower())
    for workspace in workspace_list:
        workspace['statuses'].sort(key=lambda item: status_order.get(item['id'], 999))

    return jsonify({
        'success': True,
        'workspaces': workspace_list,
        'assigned_total': assigned_total,
        'return_status_id': return_status_id,
        'close_status_id': close_status_id,
    })


@tickets_bp.route('/<int:ticket_id>', methods=['GET'])
@login_required
def view_ticket(ticket_id):
    user_id = current_user.id
    is_admin = _is_admin()

    ticket = _fetch_ticket(ticket_id, user_id, is_admin)
    if not ticket:
        abort(403)

    statuses = _fetch_statuses()
    users = _fetch_users()
    workspaces = _fetch_workspaces()
    workspace_external_map = {
        workspace['id']: bool(workspace.get('is_external'))
        for workspace in workspaces
    }
    subjobs = _fetch_subjobs(ticket_id, user_id, is_admin)
    updates = _fetch_updates(ticket_id)
    workspace = next((w for w in workspaces if w['id'] == ticket.get('workspace_id')), None)
    if workspace and workspace.get('is_external') and ticket.get('external_ticket_id'):
        response, error = _external_request(
            workspace,
            'GET',
            f"/api/external/tickets/{ticket.get('external_ticket_id')}",
        )
        if response and response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if payload and payload.get('ticket'):
                remote = payload.get('ticket')
                ticket['title'] = remote.get('title') or ticket['title']
                ticket['description'] = remote.get('description')
                ticket['status_id'] = remote.get('status_id')
                ticket['status_name'] = remote.get('status_name')
                ticket['status_is_closed'] = remote.get('status_is_closed')
                ticket['assigned_user_name'] = remote.get('assigned_user_name') or ticket.get('assigned_user_name')
                ticket['priority'] = remote.get('priority') or ticket.get('priority')
                ticket['due_date'] = remote.get('due_date')
                ticket['is_private'] = remote.get('is_private')
                updates = payload.get('updates') or updates
    return render_template(
        'ticket_edit.html',
        ticket=ticket,
        statuses=statuses,
        users=users,
        workspaces=workspaces,
        workspace_external_map=workspace_external_map,
        subjobs=subjobs,
        updates=updates,
    )


@tickets_bp.route('/<int:ticket_id>/quick-status', methods=['POST'])
@login_required
def quick_status(ticket_id):
    user_id = current_user.id
    is_admin = _is_admin()

    ticket = _fetch_ticket(ticket_id, user_id, is_admin)
    if not ticket:
        abort(403)

    if not is_admin and ticket.get('assigned_user_id') != user_id:
        return jsonify({'success': False, 'error': 'Ticket not assigned to you'}), 403

    data = request.get_json(silent=True) or {}
    status_id = data.get('status_id')
    if not status_id:
        return jsonify({'success': False, 'error': 'Status is required'}), 400

    status_row = db_execute(
        "SELECT name, is_closed FROM ticket_statuses WHERE id = ?",
        (status_id,),
        fetch="one",
    )
    if not status_row:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400

    status_name = status_row.get('name') or ''
    is_closed = bool(status_row.get('is_closed'))
    closed_at = datetime.now() if is_closed else None
    assigned_user_id = ticket.get('assigned_user_id')
    if status_name.lower() == 'returned':
        assigned_user_id = ticket.get('created_by_user_id')

    db_execute(
        """
        UPDATE tickets
        SET status_id = ?,
            assigned_user_id = ?,
            closed_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            int(status_id),
            int(assigned_user_id) if assigned_user_id else None,
            closed_at,
            ticket_id,
        ),
        commit=True,
    )

    if ticket.get('workspace_id'):
        workspace = next((w for w in _fetch_workspaces() if w['id'] == int(ticket.get('workspace_id'))), None)
    else:
        workspace = None
    if workspace and workspace.get('is_external') and ticket.get('external_ticket_id'):
        ok, error = _move_external_ticket(workspace, ticket.get('external_ticket_id'), int(status_id))
        if not ok:
            return jsonify({'success': False, 'error': error or 'External update failed.'}), 502

    if ticket.get('workspace_id'):
        workspace = next((w for w in _fetch_workspaces() if w['id'] == int(ticket.get('workspace_id'))), None)
    else:
        workspace = None
    if workspace and workspace.get('is_external') and ticket.get('external_ticket_id'):
        ok, error = _move_external_ticket(workspace, ticket.get('external_ticket_id'), int(status_id))
        if not ok:
            return jsonify({'success': False, 'error': error or 'External update failed.'}), 502

    changes = [f"Status changed to {status_name}"]
    if status_name.lower() == 'returned':
        changes.append("Returned to creator")

    _add_ticket_update(ticket_id, user_id, "; ".join(changes))

    if assigned_user_id and assigned_user_id != ticket.get('assigned_user_id'):
        _notify_assignment(
            ticket_id,
            assigned_user_id,
            ticket.get("workspace_id"),
            ticket.get("title") or "Ticket",
            bool(ticket.get("parent_ticket_id")),
            None,
        )

    # Send notification to creator if ticket was returned
    if status_name.lower() == 'returned' and ticket.get('created_by_user_id'):
        _notify_returned_ticket(
            ticket_id,
            ticket.get('created_by_user_id'),
            ticket.get("workspace_id"),
            ticket.get("title") or "Ticket",
            None,
        )

    return jsonify({'success': True})


@tickets_bp.route('/<int:ticket_id>/update', methods=['POST'])
@login_required
def update_ticket(ticket_id):
    user_id = current_user.id
    is_admin = _is_admin()

    ticket = _fetch_ticket(ticket_id, user_id, is_admin)
    if not ticket:
        abort(403)

    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    assigned_user_value = request.form.get('assigned_user_id') or None
    workspace_id = request.form.get('workspace_id') or None
    priority = request.form.get('priority', 'Medium')
    status_id = request.form.get('status_id')
    due_date = request.form.get('due_date') or None
    is_private = bool(request.form.get('is_private'))
    customer_ids = _parse_id_list(request.form.getlist('customer_ids'))
    supplier_ids = _parse_id_list(request.form.getlist('supplier_ids'))

    if not title:
        flash('Title is required.', 'error')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    if not status_id:
        flash('Status is required.', 'error')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    status_row = db_execute(
        "SELECT is_closed FROM ticket_statuses WHERE id = ?",
        (status_id,),
        fetch="one",
    )
    is_closed = status_row.get('is_closed') if status_row else False
    closed_at = datetime.now() if is_closed else None
    status_name = _status_name(status_id)

    assigned_user_id, external_assignee_id, external_assignee_name = _resolve_assignee(
        assigned_user_value,
        workspace_id,
    )

    if status_name and status_name.lower() == 'returned':
        assigned_user_id = ticket.get('created_by_user_id')
        external_assignee_id = None
        external_assignee_name = None

    db_execute(
        """
        UPDATE tickets
        SET title = ?,
            description = ?,
            status_id = ?,
            assigned_user_id = ?,
            external_assignee_id = ?,
            external_assignee_name = ?,
            workspace_id = ?,
            due_date = ?,
            is_private = ?,
            priority = ?,
            closed_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            title,
            description or None,
            int(status_id),
            int(assigned_user_id) if assigned_user_id else None,
            external_assignee_id,
            external_assignee_name,
            int(workspace_id) if workspace_id else None,
            due_date,
            is_private,
            priority,
            closed_at,
            ticket_id,
        ),
        commit=True,
    )

    workspace = next((w for w in _fetch_workspaces() if w['id'] == int(workspace_id)), None) if workspace_id else None
    if workspace and workspace.get('is_external') and ticket.get('external_ticket_id'):
        ok, error = _update_external_ticket(
            workspace,
            ticket.get('external_ticket_id'),
            {
                'title': title,
                'description': description or None,
                'status_id': int(status_id),
                'assigned_user_id': external_assignee_id,
                'priority': priority,
                'due_date': due_date,
                'is_private': bool(is_private),
            },
        )
        if not ok:
            flash(error or 'External update failed.', 'error')

    changes = []
    if int(status_id) != ticket.get('status_id'):
        changes.append(f"Status changed to {status_name}")
        if status_name and status_name.lower() == 'returned':
            changes.append("Returned to creator")
    if (int(assigned_user_id) if assigned_user_id else None) != ticket.get('assigned_user_id') or external_assignee_id != ticket.get('external_assignee_id'):
        if assigned_user_id:
            assignee_name = next((u['username'] for u in _fetch_users() if u['id'] == int(assigned_user_id)), None)
            changes.append(f"Assigned to {assignee_name or 'user #' + str(assigned_user_id)}")
        elif external_assignee_id:
            changes.append(f"Assigned to {external_assignee_name or 'external user'}")
        else:
            changes.append("Unassigned")
    existing_due = ticket.get('due_date')
    if existing_due is not None and hasattr(existing_due, "isoformat"):
        existing_due = existing_due.isoformat()
    if (due_date or None) != (existing_due or None):
        changes.append(f"Due date set to {due_date or 'not set'}")
    if priority != ticket.get('priority'):
        changes.append(f"Priority set to {priority}")
    if bool(is_private) != bool(ticket.get('is_private')):
        changes.append("Privacy updated")
    if (int(workspace_id) if workspace_id else None) != ticket.get('workspace_id'):
        if workspace_id:
            workspace_name = next(
                (w['name'] for w in _fetch_workspaces() if w['id'] == int(workspace_id)),
                None
            )
            changes.append(f"Workspace set to {workspace_name or 'workspace #' + str(workspace_id)}")
        else:
            changes.append("Workspace cleared")
    existing_customers = {
        obj.get("object_id")
        for obj in ticket.get("linked_objects", [])
        if obj.get("object_type") == "customer"
    }
    existing_suppliers = {
        obj.get("object_id")
        for obj in ticket.get("linked_objects", [])
        if obj.get("object_type") == "supplier"
    }
    if set(customer_ids) != existing_customers or set(supplier_ids) != existing_suppliers:
        changes.append("Linked objects updated")
    _sync_ticket_objects(ticket_id, customer_ids, supplier_ids)
    if changes:
        _add_ticket_update(ticket_id, user_id, "; ".join(changes))
    new_assignee_id = int(assigned_user_id) if assigned_user_id else None
    if new_assignee_id and new_assignee_id != ticket.get('assigned_user_id'):
        _notify_assignment(
            ticket_id,
            new_assignee_id,
            int(workspace_id) if workspace_id else ticket.get("workspace_id"),
            title,
            bool(ticket.get("parent_ticket_id")),
            None,
        )

    if status_name and status_name.lower() == 'returned' and ticket.get('created_by_user_id'):
        if int(status_id) != ticket.get('status_id'):
            _notify_returned_ticket(
                ticket_id,
                ticket.get('created_by_user_id'),
                int(workspace_id) if workspace_id else ticket.get("workspace_id"),
                title or "Ticket",
                None,
            )

    return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))


@tickets_bp.route('/<int:ticket_id>/subjobs', methods=['POST'])
@login_required
def add_subjob(ticket_id):
    user_id = current_user.id
    is_admin = _is_admin()

    parent = _fetch_ticket(ticket_id, user_id, is_admin)
    if not parent:
        abort(403)

    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    assigned_user_value = request.form.get('assigned_user_id') or None
    status_id = request.form.get('status_id')
    priority = request.form.get('priority', 'Medium')
    due_date = request.form.get('due_date') or None
    is_private = bool(request.form.get('is_private')) or bool(parent.get('is_private'))
    workspace_id = parent.get('workspace_id')

    if not title:
        flash('Subjob title is required.', 'error')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    if not status_id:
        flash('Status is required.', 'error')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    assigned_user_id, external_assignee_id, external_assignee_name = _resolve_assignee(
        assigned_user_value,
        workspace_id,
    )

    external_ticket_id = None
    workspace = next((w for w in _fetch_workspaces() if w['id'] == int(workspace_id)), None) if workspace_id else None
    if workspace and workspace.get('is_external'):
        external_payload = {
            'title': title,
            'description': description,
            'workspace_key': workspace.get('external_workspace_key') or workspace.get('workspace_key'),
            'priority': priority,
            'due_date': due_date,
            'is_private': bool(is_private),
            'assigned_user_id': external_assignee_id,
            'parent_ticket_id': parent.get('external_ticket_id'),
        }
        external_ticket_id, error = _create_external_ticket(workspace, external_payload)
        if error or not external_ticket_id:
            flash(error or 'Unable to create external subtask.', 'error')
            return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    row = db_execute(
        """
        INSERT INTO tickets (
            title,
            description,
            status_id,
            assigned_user_id,
            external_assignee_id,
            external_assignee_name,
            workspace_id,
            created_by_user_id,
            due_date,
            is_private,
            parent_ticket_id,
            priority,
            external_ticket_id,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        RETURNING id
        """,
        (
            title,
            description or None,
            int(status_id),
            int(assigned_user_id) if assigned_user_id else None,
            external_assignee_id,
            external_assignee_name,
            int(workspace_id) if workspace_id else None,
            user_id,
            due_date,
            is_private,
            ticket_id,
            priority,
            external_ticket_id,
        ),
        fetch="one",
        commit=True,
    )
    subjob_id = row.get('id', list(row.values())[0]) if row else None

    _add_ticket_update(ticket_id, user_id, f"Subjob added: {title}")
    if assigned_user_id and subjob_id:
        _notify_assignment(
            subjob_id,
            int(assigned_user_id),
            int(workspace_id) if workspace_id else None,
            title,
            True,
            None,
        )

    return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))


@tickets_bp.route('/users/search', methods=['GET'])
@login_required
def search_users_for_mention():
    """Search users for @mention autocomplete."""
    query = request.args.get('q', '').strip().lower()
    limit = min(int(request.args.get('limit', 8)), 20)

    if not query:
        # Return all users if no query (up to limit)
        rows = db_execute(
            "SELECT id, username, email FROM users ORDER BY username LIMIT ?",
            (limit,),
            fetch="all",
        ) or []
    else:
        # Search by username or email
        search_pattern = f"%{query}%"
        rows = db_execute(
            """
            SELECT id, username, email
            FROM users
            WHERE LOWER(username) LIKE ? OR LOWER(email) LIKE ?
            ORDER BY
                CASE WHEN LOWER(username) LIKE ? THEN 0 ELSE 1 END,
                username
            LIMIT ?
            """,
            (search_pattern, search_pattern, f"{query}%", limit),
            fetch="all",
        ) or []

    users = [
        {'id': row['id'], 'username': row['username'], 'email': row.get('email')}
        for row in rows
    ]

    return jsonify({'success': True, 'users': users})


@tickets_bp.route('/<int:ticket_id>/updates', methods=['POST'])
@login_required
def add_ticket_update(ticket_id):
    user_id = current_user.id
    is_admin = _is_admin()

    ticket = _fetch_ticket(ticket_id, user_id, is_admin)
    if not ticket:
        abort(403)

    update_text = request.form.get('update_text', '').strip()
    if not update_text:
        flash('Update text is required.', 'error')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    _add_ticket_update(ticket_id, user_id, update_text)
    if ticket.get('workspace_id'):
        workspace = next((w for w in _fetch_workspaces() if w['id'] == int(ticket.get('workspace_id'))), None)
    else:
        workspace = None
    if workspace and workspace.get('is_external') and ticket.get('external_ticket_id'):
        ok, error = _comment_external_ticket(workspace, ticket.get('external_ticket_id'), update_text)
        if not ok:
            flash(error or 'External update failed.', 'error')

    # Parse mentions and send notification emails
    mentioned_usernames = _parse_mentions(update_text)
    if mentioned_usernames:
        mentioned_users = _get_users_by_usernames(mentioned_usernames)
        _notify_mentioned_users(
            ticket_id,
            ticket.get('title', 'Ticket'),
            update_text,
            mentioned_users,
            user_id
        )

    return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))


@tickets_bp.route('/workspaces', methods=['GET', 'POST'])
@login_required
def manage_workspaces():
    if request.method == 'POST':
        logs = ['Create workspace request received.']
        name = request.form.get('name', '').strip()
        member_ids = [int(uid) for uid in request.form.getlist('member_ids') if uid.isdigit()]
        default_assignee_id = request.form.get('default_assignee_id')
        default_assignee_id = int(default_assignee_id) if default_assignee_id and str(default_assignee_id).isdigit() else None
        if not name:
            logs.append('Validation failed: workspace name missing.')
            return _workspace_response(False, 'Workspace name is required.', status=400, logs=logs)

        try:
            row = db_execute(
                """
                INSERT INTO ticket_workspaces (name, created_by_user_id, default_assignee_id, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (name, current_user.id, default_assignee_id),
                fetch="one",
                commit=True,
            )
            logs.append('Workspace insert succeeded.')
        except Exception as exc:
            logs.append(f'Workspace insert failed: {exc}')
            return _workspace_response(False, 'Workspace name already exists.', status=400, logs=logs)
        workspace_id = row.get('id', list(row.values())[0]) if row else None
        if workspace_id and member_ids:
            db_execute(
                """
                INSERT INTO ticket_workspace_members (
                    workspace_id,
                    user_id,
                    notify_ticket_assignments,
                    notify_task_assignments,
                    notify_ticket_returns,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, TRUE, TRUE, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                [(workspace_id, member_id) for member_id in member_ids],
                many=True,
                commit=True,
            )
            logs.append(f'Added {len(member_ids)} members.')
        logs.append(f'Workspace id: {workspace_id}')
        return _workspace_response(
            True,
            'Workspace created.',
            logs=logs,
            workspace={
                'id': workspace_id,
                'name': name,
                'member_ids': member_ids,
                'default_assignee_id': default_assignee_id,
            },
        )

    users = _fetch_users()
    workspaces = _fetch_workspaces_with_members()
    notification_prefs = _fetch_workspace_notifications(current_user.id)
    return render_template(
        'ticket_workspaces.html',
        workspaces=workspaces,
        users=users,
        notification_prefs=notification_prefs,
        is_admin=_is_admin(),
    )


@tickets_bp.route('/workspaces/<int:workspace_id>/share-external', methods=['POST'])
@login_required
def share_workspace_external(workspace_id):
    if not _is_admin():
        return _workspace_response(False, 'Admin access required.', status=403)

    workspace = db_execute(
        """
        SELECT id, name, workspace_key, workspace_uuid
        FROM ticket_workspaces
        WHERE id = ?
        """,
        (workspace_id,),
        fetch="one",
    )
    if not workspace:
        return _workspace_response(False, 'Workspace not found.', status=404)

    workspace = _ensure_workspace_identity(dict(workspace))
    instance_id = _get_instance_id()
    base_url = (current_app.config.get('TICKETS_BASE_URL') or request.host_url or '').strip().rstrip('/')
    if not base_url:
        return _workspace_response(False, 'TICKETS_BASE_URL is required.', status=400)

    payload = {
        'remote_instance_id': instance_id,
        'remote_base_url': base_url,
        'remote_workspace_uuid': workspace['workspace_uuid'],
        'remote_workspace_key': workspace['workspace_key'],
        'remote_workspace_name': workspace['name'],
    }
    token = _encode_import_token(payload)
    return _workspace_response(
        True,
        'Import token generated.',
        status=200,
        token=token,
    )


@tickets_bp.route('/workspaces/import-external', methods=['POST'])
@login_required
def import_external_workspace():
    if not _is_admin():
        return _workspace_response(False, 'Admin access required.', status=403)

    token = (request.form.get('token') or '').strip()
    if not token:
        return _workspace_response(False, 'Import token is required.', status=400)

    payload = _decode_import_token(token)
    if not payload:
        return _workspace_response(False, 'Invalid import token.', status=400)

    missing = _validate_import_payload(payload)
    if missing:
        return _workspace_response(False, 'Import token is missing fields.', status=400, missing=missing)

    remote_workspace_key = payload['remote_workspace_key'].strip().lower()
    local_workspace_key = (request.form.get('local_workspace_key') or remote_workspace_key).strip().lower()
    local_workspace_name = (request.form.get('local_workspace_name') or payload['remote_workspace_name']).strip()

    if not local_workspace_key:
        local_workspace_key = _slugify(local_workspace_name)

    existing = db_execute(
        "SELECT id FROM ticket_workspaces WHERE workspace_key = ?",
        (local_workspace_key,),
        fetch="one",
    )
    if existing:
        db_execute(
            """
            UPDATE ticket_workspaces
            SET
                name = ?,
                is_external = TRUE,
                external_instance_id = ?,
                external_base_url = ?,
                external_workspace_uuid = ?,
                external_workspace_key = ?
            WHERE id = ?
            """,
            (
                local_workspace_name,
                payload['remote_instance_id'],
                payload['remote_base_url'],
                payload['remote_workspace_uuid'],
                remote_workspace_key,
                existing['id'],
            ),
            commit=True,
        )
        ok, error = _register_workspace_user_link(
            existing['id'],
            payload['remote_workspace_uuid'],
            local_workspace_key,
        )
        message = 'Workspace updated.'
        if not ok:
            message = f'Workspace updated, but user sync link failed: {error}'
        return _workspace_response(True, message, status=200, workspace_id=existing['id'])

    name_candidate = local_workspace_name
    suffix = 2
    while True:
        name_conflict = db_execute(
            "SELECT id FROM ticket_workspaces WHERE name = ?",
            (name_candidate,),
            fetch="one",
        )
        if not name_conflict:
            local_workspace_name = name_candidate
            break
        name_candidate = f"{local_workspace_name} ({suffix})"
        suffix += 1

    row = db_execute(
        """
        INSERT INTO ticket_workspaces (
            name,
            created_by_user_id,
            created_at,
            workspace_key,
            workspace_uuid,
            is_external,
            external_instance_id,
            external_base_url,
            external_workspace_uuid,
            external_workspace_key
        )
        VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, TRUE, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            local_workspace_name,
            current_user.id,
            local_workspace_key,
            str(uuid.uuid4()),
            payload['remote_instance_id'],
            payload['remote_base_url'],
            payload['remote_workspace_uuid'],
            remote_workspace_key,
        ),
        fetch="one",
        commit=True,
    )
    workspace_id = row.get('id', list(row.values())[0]) if row else None
    if workspace_id:
        ok, error = _register_workspace_user_link(
            workspace_id,
            payload['remote_workspace_uuid'],
            local_workspace_key,
        )
        message = 'Workspace imported.'
        if not ok:
            message = f'Workspace imported, but user sync link failed: {error}'
        return _workspace_response(True, message, status=201, workspace_id=workspace_id)
    return _workspace_response(True, 'Workspace imported.', status=201, workspace_id=workspace_id)


@tickets_bp.route('/workspaces/<int:workspace_id>/external-users', methods=['GET'])
@login_required
def external_workspace_users(workspace_id):
    workspace = db_execute(
        """
        SELECT
            id,
            is_external,
            external_instance_id,
            external_base_url,
            external_workspace_uuid
        FROM ticket_workspaces
        WHERE id = ?
        """,
        (workspace_id,),
        fetch="one",
    )
    if not workspace or not workspace.get('is_external'):
        return jsonify({'success': True, 'users': []})

    ok, result = _refresh_external_users(dict(workspace))
    error_message = None
    if not ok:
        error_message = result

    rows = db_execute(
        """
        SELECT external_user_id AS id, display_name, email
        FROM external_ticket_users
        WHERE external_instance_id = ? AND external_workspace_uuid = ?
        ORDER BY display_name
        """,
        (workspace.get('external_instance_id'), workspace.get('external_workspace_uuid')),
        fetch="all",
    ) or []
    users = [dict(row) for row in rows]
    payload = {'success': True, 'users': users}
    if error_message:
        payload['error'] = error_message
    return jsonify(payload)


@tickets_bp.route('/api/external/workspaces/<workspace_uuid>/users', methods=['GET'])
def external_workspace_users_api(workspace_uuid):
    if not _external_api_authorized():
        return jsonify({'error': 'Unauthorized'}), 401

    workspace = db_execute(
        """
        SELECT id, workspace_uuid
        FROM ticket_workspaces
        WHERE workspace_uuid = ?
        """,
        (workspace_uuid,),
        fetch="one",
    )
    if not workspace:
        workspace_key = (request.args.get('workspace_key') or '').strip().lower()
        if workspace_key:
            workspace = db_execute(
                """
                SELECT id, workspace_uuid
                FROM ticket_workspaces
                WHERE workspace_key = ?
                """,
                (workspace_key,),
                fetch="one",
            )
            if workspace and not workspace.get('workspace_uuid'):
                db_execute(
                    "UPDATE ticket_workspaces SET workspace_uuid = ? WHERE id = ?",
                    (workspace_uuid, workspace['id']),
                    commit=True,
                )
                workspace['workspace_uuid'] = workspace_uuid
            elif workspace and workspace.get('workspace_uuid') and workspace.get('workspace_uuid') != workspace_uuid:
                return jsonify({'error': 'Workspace UUID mismatch.'}), 409
    if not workspace:
        return jsonify({'error': 'Workspace not found'}), 404

    rows = db_execute(
        """
        SELECT u.id, u.username, u.email
        FROM ticket_workspace_members twm
        JOIN users u ON u.id = twm.user_id
        WHERE twm.workspace_id = ?
        ORDER BY u.username
        """,
        (workspace['id'],),
        fetch="all",
    ) or []
    users = [
        {
            'id': str(row['id']),
            'display_name': row.get('username') or row.get('email') or 'User',
            'email': row.get('email'),
        }
        for row in rows
    ]
    return jsonify({'users': users})


@tickets_bp.route('/workspaces/<int:workspace_id>', methods=['POST'])
@login_required
def update_workspace(workspace_id):
    logs = [f'Update workspace {workspace_id} request received.']
    name = request.form.get('name', '').strip()
    member_ids = [int(uid) for uid in request.form.getlist('member_ids') if uid.isdigit()]
    default_assignee_id = request.form.get('default_assignee_id')
    default_assignee_id = int(default_assignee_id) if default_assignee_id and str(default_assignee_id).isdigit() else None
    if not name:
        logs.append('Validation failed: workspace name missing.')
        return _workspace_response(False, 'Workspace name is required.', status=400, logs=logs)

    try:
        db_execute(
            """
            UPDATE ticket_workspaces
            SET name = ?, default_assignee_id = ?
            WHERE id = ?
            """,
            (name, default_assignee_id, workspace_id),
            commit=True,
        )
        logs.append('Workspace update succeeded.')
    except Exception as exc:
        logs.append(f'Workspace update failed: {exc}')
        return _workspace_response(False, 'Workspace name already exists.', status=400, logs=logs)
    existing_members = db_execute(
        """
        SELECT
            user_id,
            COALESCE(notify_ticket_assignments, FALSE) AS notify_ticket_assignments,
            COALESCE(notify_task_assignments, FALSE) AS notify_task_assignments,
            COALESCE(notify_ticket_returns, FALSE) AS notify_ticket_returns
        FROM ticket_workspace_members
        WHERE workspace_id = ?
        """,
        (workspace_id,),
        fetch="all",
    ) or []
    existing_pref_map = {
        row["user_id"]: {
            "notify_ticket_assignments": bool(row.get("notify_ticket_assignments")),
            "notify_task_assignments": bool(row.get("notify_task_assignments")),
            "notify_ticket_returns": bool(row.get("notify_ticket_returns")),
        }
        for row in existing_members
    }
    db_execute(
        "DELETE FROM ticket_workspace_members WHERE workspace_id = ?",
        (workspace_id,),
        commit=True,
    )
    logs.append('Cleared existing members.')
    if member_ids:
        db_execute(
            """
            INSERT INTO ticket_workspace_members (
                workspace_id,
                user_id,
                notify_ticket_assignments,
                notify_task_assignments,
                notify_ticket_returns,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            [
                (
                    workspace_id,
                    member_id,
                    existing_pref_map.get(member_id, {}).get("notify_ticket_assignments", False),
                    existing_pref_map.get(member_id, {}).get("notify_task_assignments", False),
                    existing_pref_map.get(member_id, {}).get("notify_ticket_returns", False),
                )
                for member_id in member_ids
            ],
            many=True,
            commit=True,
        )
        logs.append(f'Added {len(member_ids)} members.')
    return _workspace_response(
        True,
        'Workspace updated.',
        logs=logs,
        workspace={
            'id': workspace_id,
            'name': name,
            'member_ids': member_ids,
            'default_assignee_id': default_assignee_id,
        },
    )


@tickets_bp.route('/workspaces/<int:workspace_id>/notifications', methods=['POST'])
@login_required
def update_workspace_notifications(workspace_id):
    user_id = current_user.id
    row = db_execute(
        "SELECT 1 FROM ticket_workspace_members WHERE workspace_id = ? AND user_id = ?",
        (workspace_id, user_id),
        fetch="one",
    )
    if not row:
        return jsonify({'success': False, 'message': 'You must be a member of this workspace.'}), 403

    payload = request.get_json(silent=True) or request.form
    notify_tickets = _parse_bool(payload.get('notify_ticket_assignments'))
    notify_tasks = _parse_bool(payload.get('notify_task_assignments'))
    notify_returns = _parse_bool(payload.get('notify_ticket_returns'))
    db_execute(
        """
        UPDATE ticket_workspace_members
        SET notify_ticket_assignments = ?,
            notify_task_assignments = ?,
            notify_ticket_returns = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE workspace_id = ? AND user_id = ?
        """,
        (notify_tickets, notify_tasks, notify_returns, workspace_id, user_id),
        commit=True,
    )
    return jsonify({'success': True, 'message': 'Notification preferences saved.'})


@tickets_bp.route('/<int:ticket_id>/move', methods=['POST'])
@login_required
def move_ticket(ticket_id):
    """Update ticket status when dragged between columns."""
    user_id = current_user.id
    is_admin = _is_admin()

    ticket = _fetch_ticket(ticket_id, user_id, is_admin)
    if not ticket:
        return jsonify({'success': False, 'error': 'Ticket not found or access denied'}), 404

    data = request.get_json(silent=True) or {}
    status_id = data.get('status_id')
    if not status_id:
        return jsonify({'success': False, 'error': 'Status ID is required'}), 400

    status_row = db_execute(
        "SELECT name, is_closed FROM ticket_statuses WHERE id = ?",
        (status_id,),
        fetch="one",
    )
    if not status_row:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400

    status_name = status_row.get('name') or ''
    is_closed = bool(status_row.get('is_closed'))
    closed_at = datetime.now() if is_closed else None
    
    # Optional logic: If moving to "Returned", reassign to creator
    assigned_user_id = ticket.get('assigned_user_id')
    if status_name.lower() == 'returned':
        assigned_user_id = ticket.get('created_by_user_id')

    db_execute(
        """
        UPDATE tickets
        SET status_id = ?,
            assigned_user_id = ?,
            closed_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            int(status_id),
            int(assigned_user_id) if assigned_user_id else None,
            closed_at,
            ticket_id,
        ),
        commit=True,
    )

    changes = [f"Status changed to {status_name}"]
    if status_name.lower() == 'returned' and assigned_user_id != ticket.get('assigned_user_id'):
        changes.append("Returned to creator")
    
    _add_ticket_update(ticket_id, user_id, "; ".join(changes))
    
    if assigned_user_id and assigned_user_id != ticket.get('assigned_user_id'):
        _notify_assignment(
            ticket_id,
            assigned_user_id,
            ticket.get("workspace_id"),
            ticket.get("title") or "Ticket",
            bool(ticket.get("parent_ticket_id")),
            None,
        )

    if status_name.lower() == 'returned' and ticket.get('created_by_user_id'):
        _notify_returned_ticket(
            ticket_id,
            ticket.get('created_by_user_id'),
            ticket.get("workspace_id"),
            ticket.get("title") or "Ticket",
            None,
        )

    return jsonify({
        'success': True, 
        'message': f"Ticket moved to {status_name}",
        'is_closed': is_closed,
        'assigned_user_id': assigned_user_id
    })
