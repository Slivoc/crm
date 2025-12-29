import json
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, abort, flash, jsonify
from db import execute as db_execute, db_cursor
from routes.auth import login_required, current_user
from models import Permission


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


def _fetch_ticket(ticket_id, user_id, is_admin):
    visibility_clause, params = _ticket_visibility_clause(user_id, is_admin)
    row = db_execute(
        f"""
        SELECT
            t.*,
            s.name AS status_name,
            s.is_closed AS status_is_closed,
            au.username AS assigned_user_name,
            cu.username AS created_by_name,
            pt.title AS parent_title,
            pt.is_private AS parent_is_private,
            pt.created_by_user_id AS parent_created_by_user_id,
            pt.assigned_user_id AS parent_assigned_user_id,
            tw.name AS workspace_name,
            COALESCE(tlc.link_count, 0) AS link_count
        FROM tickets t
        JOIN ticket_statuses s ON t.status_id = s.id
        JOIN users cu ON t.created_by_user_id = cu.id
        LEFT JOIN users au ON t.assigned_user_id = au.id
        LEFT JOIN tickets pt ON t.parent_ticket_id = pt.id
        LEFT JOIN ticket_workspaces tw ON t.workspace_id = tw.id
        LEFT JOIN (
            SELECT ticket_id, COUNT(*) AS link_count
            FROM ticket_links
            GROUP BY ticket_id
        ) tlc ON tlc.ticket_id = t.id
        WHERE t.id = ?
        {visibility_clause}
        """,
        [ticket_id] + params,
        fetch="one",
    )
    if not row:
        return None
    ticket = dict(row)
    ticket["link_count"] = int(ticket.get("link_count") or 0)
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
            au.username AS assigned_user_name,
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
        "SELECT id, username FROM users ORDER BY username",
        fetch="all",
    ) or []
    return [dict(row) for row in rows]


def _fetch_workspaces():
    rows = db_execute(
        "SELECT id, name FROM ticket_workspaces ORDER BY name",
        fetch="all",
    ) or []
    return [dict(row) for row in rows]


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


def _fetch_customers_for_links():
    rows = db_execute(
        "SELECT id, name FROM customers ORDER BY name",
        fetch="all",
    ) or []
    return [dict(row) for row in rows]


def _fetch_suppliers_for_links():
    rows = db_execute(
        "SELECT id, name FROM suppliers ORDER BY name",
        fetch="all",
    ) or []
    return [dict(row) for row in rows]


def _parse_links_payload(raw_value):
    if not raw_value:
        return []
    try:
        payload = json.loads(raw_value)
    except Exception:
        return []
    links = []
    seen = set()
    for item in payload if isinstance(payload, list) else []:
        link_type = (item.get('type') or '').lower()
        object_id = item.get('id')
        try:
            object_id = int(object_id)
        except (TypeError, ValueError):
            continue
        if link_type not in ('customer', 'supplier'):
            continue
        key = (link_type, object_id)
        if key in seen:
            continue
        seen.add(key)
        links.append({'link_type': link_type, 'object_id': object_id})
    return links


def _filter_valid_links(links):
    customers = [link['object_id'] for link in links if link['link_type'] == 'customer']
    suppliers = [link['object_id'] for link in links if link['link_type'] == 'supplier']
    valid_customers = set()
    valid_suppliers = set()

    if customers:
        placeholders = ", ".join(["?"] * len(customers))
        rows = db_execute(
            f"SELECT id FROM customers WHERE id IN ({placeholders})",
            customers,
            fetch="all",
        ) or []
        valid_customers = {row['id'] for row in rows}

    if suppliers:
        placeholders = ", ".join(["?"] * len(suppliers))
        rows = db_execute(
            f"SELECT id FROM suppliers WHERE id IN ({placeholders})",
            suppliers,
            fetch="all",
        ) or []
        valid_suppliers = {row['id'] for row in rows}

    filtered = []
    for link in links:
        if link['link_type'] == 'customer' and link['object_id'] not in valid_customers:
            continue
        if link['link_type'] == 'supplier' and link['object_id'] not in valid_suppliers:
            continue
        filtered.append(link)
    return filtered


def _apply_ticket_links(ticket_id, links):
    if ticket_id is None:
        return
    links = _filter_valid_links(links)
    with db_cursor(commit=True) as cur:
        cur.execute(
            "SELECT id, link_type, object_id FROM ticket_links WHERE ticket_id = ?",
            (ticket_id,),
        )
        rows = cur.fetchall() or []
        existing = {(row["link_type"], row["object_id"]): row["id"] for row in rows}
        desired = {(link["link_type"], link["object_id"]) for link in links}

        to_delete = [link_id for key, link_id in existing.items() if key not in desired]
        if to_delete:
            placeholders = ", ".join(["?"] * len(to_delete))
            cur.execute(
                f"DELETE FROM ticket_links WHERE id IN ({placeholders})",
                to_delete,
            )

        to_insert = [
            link for link in links
            if (link["link_type"], link["object_id"]) not in existing
        ]
        if to_insert:
            cur.executemany(
                """
                INSERT INTO ticket_links (ticket_id, link_type, object_id)
                VALUES (?, ?, ?)
                """,
                [(ticket_id, link["link_type"], link["object_id"]) for link in to_insert],
            )


def _fetch_ticket_links(ticket_id):
    rows = db_execute(
        """
        SELECT
            tl.id,
            tl.link_type,
            tl.object_id,
            CASE
                WHEN tl.link_type = 'customer' THEN c.name
                WHEN tl.link_type = 'supplier' THEN s.name
                ELSE NULL
            END AS object_name
        FROM ticket_links tl
        LEFT JOIN customers c ON tl.link_type = 'customer' AND c.id = tl.object_id
        LEFT JOIN suppliers s ON tl.link_type = 'supplier' AND s.id = tl.object_id
        WHERE tl.ticket_id = ?
        ORDER BY tl.created_at, tl.id
        """,
        (ticket_id,),
        fetch="all",
    ) or []
    return [dict(row) for row in rows]


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
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        assigned_user_id = request.form.get('assigned_user_id') or None
        workspace_id = request.form.get('workspace_id') or None
        status_id = request.form.get('status_id')
        due_date = request.form.get('due_date') or None
        is_private = bool(request.form.get('is_private'))
        linked_items = _parse_links_payload(request.form.get('linked_items'))

        if not title:
            flash('Title is required.', 'error')
            return redirect(url_for('tickets.list_tickets'))

        if not status_id:
            flash('Status is required.', 'error')
            return redirect(url_for('tickets.list_tickets'))

        row = db_execute(
            """
            INSERT INTO tickets (
                title,
                description,
                status_id,
                assigned_user_id,
                workspace_id,
                created_by_user_id,
                due_date,
                is_private,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (
                title,
                description or None,
                int(status_id),
                int(assigned_user_id) if assigned_user_id else None,
                int(workspace_id) if workspace_id else None,
                user_id,
                due_date,
                is_private,
            ),
            fetch="one",
            commit=True,
        )
        ticket_id = row.get('id', list(row.values())[0]) if row else None
        if ticket_id is not None:
            _apply_ticket_links(ticket_id, linked_items)
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    show_closed = _parse_bool(request.args.get('show_closed'))
    only_mine = _parse_bool(request.args.get('only_mine'))
    created_by_me = _parse_bool(request.args.get('created_by_me'))
    if request.args.get('only_mine') is None and request.args.get('created_by_me') is None:
        only_mine = True
    workspace_filter_id = request.args.get('workspace_id') or None

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
            au.username AS assigned_user_name,
            cu.username AS created_by_name,
            pt.id AS parent_id,
            pt.title AS parent_title,
            pt.parent_ticket_id AS parent_parent_id,
            tw.name AS workspace_name,
            COALESCE(sc.subjob_count, 0) AS subjob_count,
            COALESCE(sc.closed_subjob_count, 0) AS closed_subjob_count,
            COALESCE(tlc.link_count, 0) AS link_count
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
        LEFT JOIN (
            SELECT ticket_id, COUNT(*) AS link_count
            FROM ticket_links
            GROUP BY ticket_id
        ) tlc ON tlc.ticket_id = t.id
        WHERE 1 = 1
        {visibility_clause}
        {parent_visibility_clause}
        {closed_clause}
        {mine_clause}
        {workspace_clause}
        ORDER BY
            CASE WHEN t.due_date IS NULL THEN 1 ELSE 0 END,
            t.due_date ASC,
            t.updated_at DESC
        """,
        visibility_params + parent_visibility_params + mine_params + workspace_params,
        fetch="all",
    ) or []

    tickets = [dict(row) for row in rows]
    ticket_lookup = {ticket["id"]: ticket for ticket in tickets}
    for ticket in tickets:
        ticket["link_count"] = int(ticket.get("link_count") or 0)
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
    statuses = _fetch_statuses()
    users = _fetch_users()
    workspaces = _fetch_workspaces()
    workspace_chips = _fetch_workspace_chips(user_id)
    customers = _fetch_customers_for_links()
    suppliers = _fetch_suppliers_for_links()
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
        default_status_id=default_status_id,
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
    return_status_id = next(
        (status['id'] for status in statuses if status.get('name', '').lower() == 'returned'),
        None,
    )
    close_status_id = next(
        (status['id'] for status in statuses if status.get('is_closed')),
        None,
    )

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
        {visibility_clause}
        """,
        [user_id] + visibility_params,
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
            {visibility_clause}
            """,
            batch + visibility_params,
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
    subjobs = _fetch_subjobs(ticket_id, user_id, is_admin)
    updates = _fetch_updates(ticket_id)
    ticket_links = _fetch_ticket_links(ticket_id)
    customers = _fetch_customers_for_links()
    suppliers = _fetch_suppliers_for_links()
    ticket["link_count"] = len(ticket_links)

    return render_template(
        'ticket_edit.html',
        ticket=ticket,
        statuses=statuses,
        users=users,
        workspaces=workspaces,
        subjobs=subjobs,
        updates=updates,
        ticket_links=ticket_links,
        customers=customers,
        suppliers=suppliers,
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

    changes = [f"Status changed to {status_name}"]
    if status_name.lower() == 'returned':
        changes.append("Returned to creator")
    if assigned_user_id != ticket.get('assigned_user_id'):
        changes.append("Assignee updated")
    _add_ticket_update(ticket_id, user_id, "; ".join(changes))

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
    assigned_user_id = request.form.get('assigned_user_id') or None
    workspace_id = request.form.get('workspace_id') or None
    status_id = request.form.get('status_id')
    due_date = request.form.get('due_date') or None
    is_private = bool(request.form.get('is_private'))
    linked_items = _parse_links_payload(request.form.get('linked_items'))
    current_links = _fetch_ticket_links(ticket_id)
    current_link_pairs = {(link.get("link_type"), link.get("object_id")) for link in current_links}
    desired_link_pairs = {(link.get("link_type"), link.get("object_id")) for link in linked_items}

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

    if status_name and status_name.lower() == 'returned':
        assigned_user_id = ticket.get('created_by_user_id')

    db_execute(
        """
        UPDATE tickets
        SET title = ?,
            description = ?,
            status_id = ?,
            assigned_user_id = ?,
            workspace_id = ?,
            due_date = ?,
            is_private = ?,
            closed_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            title,
            description or None,
            int(status_id),
            int(assigned_user_id) if assigned_user_id else None,
            int(workspace_id) if workspace_id else None,
            due_date,
            is_private,
            closed_at,
            ticket_id,
        ),
        commit=True,
    )

    _apply_ticket_links(ticket_id, linked_items)

    changes = []
    if int(status_id) != ticket.get('status_id'):
        changes.append(f"Status changed to {status_name}")
        if status_name and status_name.lower() == 'returned':
            changes.append("Returned to creator")
    if (int(assigned_user_id) if assigned_user_id else None) != ticket.get('assigned_user_id'):
        if assigned_user_id:
            assignee_name = next((u['username'] for u in _fetch_users() if u['id'] == int(assigned_user_id)), None)
            changes.append(f"Assigned to {assignee_name or 'user #' + str(assigned_user_id)}")
        else:
            changes.append("Unassigned")
    existing_due = ticket.get('due_date')
    if existing_due is not None and hasattr(existing_due, "isoformat"):
        existing_due = existing_due.isoformat()
    if (due_date or None) != (existing_due or None):
        changes.append(f"Due date set to {due_date or 'not set'}")
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
    if current_link_pairs != desired_link_pairs:
        changes.append("Links updated")
    if changes:
        _add_ticket_update(ticket_id, user_id, "; ".join(changes))

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
    assigned_user_id = request.form.get('assigned_user_id') or None
    status_id = request.form.get('status_id')
    due_date = request.form.get('due_date') or None
    is_private = bool(request.form.get('is_private')) or bool(parent.get('is_private'))
    workspace_id = parent.get('workspace_id')

    if not title:
        flash('Subjob title is required.', 'error')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    if not status_id:
        flash('Status is required.', 'error')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    db_execute(
        """
        INSERT INTO tickets (
            title,
            description,
            status_id,
            assigned_user_id,
            workspace_id,
            created_by_user_id,
            due_date,
            is_private,
            parent_ticket_id,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            title,
            description or None,
            int(status_id),
            int(assigned_user_id) if assigned_user_id else None,
            int(workspace_id) if workspace_id else None,
            user_id,
            due_date,
            is_private,
            ticket_id,
        ),
        commit=True,
    )

    _add_ticket_update(ticket_id, user_id, f"Subjob added: {title}")

    return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))


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

    return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))


@tickets_bp.route('/workspaces', methods=['GET', 'POST'])
@login_required
def manage_workspaces():
    if request.method == 'POST':
        logs = ['Create workspace request received.']
        name = request.form.get('name', '').strip()
        member_ids = [int(uid) for uid in request.form.getlist('member_ids') if uid.isdigit()]
        if not name:
            logs.append('Validation failed: workspace name missing.')
            return _workspace_response(False, 'Workspace name is required.', status=400, logs=logs)

        try:
            row = db_execute(
                """
                INSERT INTO ticket_workspaces (name, created_by_user_id, created_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (name, current_user.id),
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
                INSERT INTO ticket_workspace_members (workspace_id, user_id, created_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
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
            workspace={'id': workspace_id, 'name': name, 'member_ids': member_ids},
        )

    users = _fetch_users()
    workspaces = _fetch_workspaces_with_members()
    return render_template(
        'ticket_workspaces.html',
        workspaces=workspaces,
        users=users,
    )


@tickets_bp.route('/workspaces/<int:workspace_id>', methods=['POST'])
@login_required
def update_workspace(workspace_id):
    logs = [f'Update workspace {workspace_id} request received.']
    name = request.form.get('name', '').strip()
    member_ids = [int(uid) for uid in request.form.getlist('member_ids') if uid.isdigit()]
    if not name:
        logs.append('Validation failed: workspace name missing.')
        return _workspace_response(False, 'Workspace name is required.', status=400, logs=logs)

    try:
        db_execute(
            """
            UPDATE ticket_workspaces
            SET name = ?
            WHERE id = ?
            """,
            (name, workspace_id),
            commit=True,
        )
        logs.append('Workspace update succeeded.')
    except Exception as exc:
        logs.append(f'Workspace update failed: {exc}')
        return _workspace_response(False, 'Workspace name already exists.', status=400, logs=logs)
    db_execute(
        "DELETE FROM ticket_workspace_members WHERE workspace_id = ?",
        (workspace_id,),
        commit=True,
    )
    logs.append('Cleared existing members.')
    if member_ids:
        db_execute(
            """
            INSERT INTO ticket_workspace_members (workspace_id, user_id, created_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            [(workspace_id, member_id) for member_id in member_ids],
            many=True,
            commit=True,
        )
        logs.append(f'Added {len(member_ids)} members.')
    return _workspace_response(
        True,
        'Workspace updated.',
        logs=logs,
        workspace={'id': workspace_id, 'name': name, 'member_ids': member_ids},
    )
