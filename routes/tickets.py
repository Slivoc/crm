from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, abort, flash, jsonify
from db import execute as db_execute
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
        if ticket.get("parent_id") and ticket.get("parent_title"):
            ticket["parent_label"] = f"#{ticket['parent_id']} {ticket['parent_title']}"
        else:
            ticket["parent_label"] = None
    statuses = _fetch_statuses()
    users = _fetch_users()
    workspaces = _fetch_workspaces()
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
        show_closed=show_closed,
        only_mine=only_mine,
        created_by_me=created_by_me,
        workspace_filter_id=workspace_filter_id,
        default_status_id=default_status_id,
    )


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

    return render_template(
        'ticket_edit.html',
        ticket=ticket,
        statuses=statuses,
        users=users,
        workspaces=workspaces,
        subjobs=subjobs,
        updates=updates,
    )


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
        name = request.form.get('name', '').strip()
        member_ids = [int(uid) for uid in request.form.getlist('member_ids') if uid.isdigit()]
        if not name:
            flash('Workspace name is required.', 'error')
            return redirect(url_for('tickets.manage_workspaces'))

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
        except Exception:
            flash('Workspace name already exists.', 'error')
            return redirect(url_for('tickets.manage_workspaces'))
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
        flash('Workspace created.', 'success')
        return redirect(url_for('tickets.manage_workspaces'))

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
    name = request.form.get('name', '').strip()
    member_ids = [int(uid) for uid in request.form.getlist('member_ids') if uid.isdigit()]
    if not name:
        flash('Workspace name is required.', 'error')
        return redirect(url_for('tickets.manage_workspaces'))

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
    except Exception:
        flash('Workspace name already exists.', 'error')
        return redirect(url_for('tickets.manage_workspaces'))
    db_execute(
        "DELETE FROM ticket_workspace_members WHERE workspace_id = ?",
        (workspace_id,),
        commit=True,
    )
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
    flash('Workspace updated.', 'success')
    return redirect(url_for('tickets.manage_workspaces'))
