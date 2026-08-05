import random
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from db import db_cursor, execute as db_execute
from models import Permission
from routes.auth import current_user, login_required


problems_bp = Blueprint("problems", __name__)

PROBLEM_STATUSES = (
    ("open", "Open"),
    ("investigating", "Investigating"),
    ("waiting", "Waiting"),
    ("resolved", "Resolved"),
)
STATUS_LABELS = dict(PROBLEM_STATUSES)


@problems_bp.before_request
def require_login():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login", next=request.url))


def _is_admin():
    try:
        return current_user.can(Permission.ADMIN)
    except Exception:
        return False


def _ticket_visibility_sql(alias="t"):
    if _is_admin():
        return "", []
    return (
        f"AND ({alias}.is_private = FALSE OR {alias}.created_by_user_id = ? "
        f"OR {alias}.assigned_user_id = ? OR {alias}.external_assignee_id = ?)",
        [current_user.id, current_user.id, str(current_user.id)],
    )


def _parse_id(value):
    try:
        value = int(value)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_id_list(values):
    result = []
    seen = set()
    for value in values or []:
        parsed = _parse_id(value)
        if parsed and parsed not in seen:
            result.append(parsed)
            seen.add(parsed)
    return result


def _fetch_types(active_only=False):
    clause = "WHERE is_active = TRUE" if active_only else ""
    return [dict(row) for row in (db_execute(
        f"SELECT id, name, is_active FROM problem_types {clause} ORDER BY sort_order, name",
        fetch="all",
    ) or [])]


def _fetch_causes(active_only=False):
    clause = "WHERE is_active = TRUE" if active_only else ""
    return [dict(row) for row in (db_execute(
        f"""
        SELECT id, code, name, party_type, is_active
        FROM problem_cause_categories
        {clause}
        ORDER BY sort_order, name
        """,
        fetch="all",
    ) or [])]


def _fetch_users():
    return [dict(row) for row in (db_execute(
        "SELECT id, username, email FROM users ORDER BY username",
        fetch="all",
    ) or [])]


def _fetch_problem(problem_id):
    row = db_execute(
        """
        SELECT
            p.*,
            pt.name AS problem_type_name,
            pc.name AS cause_category_name,
            pc.party_type AS cause_party_type,
            au.username AS assigned_user_name,
            cu.username AS created_by_name,
            CASE
                WHEN pc.party_type = 'customer' THEN cause_customer.name
                WHEN pc.party_type = 'supplier' THEN cause_supplier.name
                WHEN pc.party_type = 'user' THEN cause_user.username
                ELSE NULL
            END AS cause_object_name,
            EXTRACT(EPOCH FROM (COALESCE(p.resolved_at, CURRENT_TIMESTAMP) - p.created_at)) / 86400.0 AS age_days
        FROM problems p
        JOIN problem_types pt ON pt.id = p.problem_type_id
        JOIN problem_cause_categories pc ON pc.id = p.cause_category_id
        JOIN users cu ON cu.id = p.created_by_user_id
        LEFT JOIN users au ON au.id = p.assigned_user_id
        LEFT JOIN customers cause_customer
            ON pc.party_type = 'customer' AND cause_customer.id = p.cause_object_id
        LEFT JOIN suppliers cause_supplier
            ON pc.party_type = 'supplier' AND cause_supplier.id = p.cause_object_id
        LEFT JOIN users cause_user
            ON pc.party_type = 'user' AND cause_user.id = p.cause_object_id
        WHERE p.id = ?
        """,
        (problem_id,),
        fetch="one",
    )
    return dict(row) if row else None


def _fetch_problem_objects(problem_id):
    rows = db_execute(
        """
        SELECT
            po.object_type,
            po.object_id,
            CASE
                WHEN po.object_type = 'customer' THEN c.name
                WHEN po.object_type = 'supplier' THEN s.name
                ELSE NULL
            END AS object_name
        FROM problem_objects po
        LEFT JOIN customers c ON po.object_type = 'customer' AND c.id = po.object_id
        LEFT JOIN suppliers s ON po.object_type = 'supplier' AND s.id = po.object_id
        WHERE po.problem_id = ?
        ORDER BY po.object_type, object_name
        """,
        (problem_id,),
        fetch="all",
    ) or []
    return [dict(row) for row in rows]


def _fetch_objects_for_problems(problem_ids):
    if not problem_ids:
        return {}
    placeholders = ", ".join(["?"] * len(problem_ids))
    rows = db_execute(
        f"""
        SELECT
            po.problem_id,
            po.object_type,
            po.object_id,
            CASE
                WHEN po.object_type = 'customer' THEN c.name
                WHEN po.object_type = 'supplier' THEN s.name
                ELSE NULL
            END AS object_name
        FROM problem_objects po
        LEFT JOIN customers c ON po.object_type = 'customer' AND c.id = po.object_id
        LEFT JOIN suppliers s ON po.object_type = 'supplier' AND s.id = po.object_id
        WHERE po.problem_id IN ({placeholders})
        ORDER BY po.object_type, object_name
        """,
        problem_ids,
        fetch="all",
    ) or []
    grouped = {}
    for row in rows:
        grouped.setdefault(row["problem_id"], []).append(dict(row))
    return grouped


def _sync_problem_objects(problem_id, customer_ids, supplier_ids):
    desired = {("customer", value) for value in customer_ids}
    desired.update({("supplier", value) for value in supplier_ids})
    rows = db_execute(
        "SELECT object_type, object_id FROM problem_objects WHERE problem_id = ?",
        (problem_id,),
        fetch="all",
    ) or []
    existing = {(row["object_type"], int(row["object_id"])) for row in rows}

    remove = existing - desired
    add = desired - existing
    if remove:
        db_execute(
            "DELETE FROM problem_objects WHERE problem_id = ? AND object_type = ? AND object_id = ?",
            [(problem_id, object_type, object_id) for object_type, object_id in remove],
            many=True,
            commit=True,
        )
    if add:
        db_execute(
            """
            INSERT INTO problem_objects (problem_id, object_type, object_id)
            VALUES (?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [(problem_id, object_type, object_id) for object_type, object_id in add],
            many=True,
            commit=True,
        )


def _cause_selection(cause_category_id, raw_object_id):
    cause = db_execute(
        "SELECT id, name, party_type FROM problem_cause_categories WHERE id = ? AND is_active = TRUE",
        (cause_category_id,),
        fetch="one",
    )
    if not cause:
        return None, None, "Choose a valid cause."

    object_id = _parse_id(raw_object_id)
    party_type = cause.get("party_type")
    if not party_type:
        return dict(cause), None, None
    if not object_id:
        return dict(cause), None, None

    table = {"customer": "customers", "supplier": "suppliers", "user": "users"}[party_type]
    exists = db_execute(f"SELECT id FROM {table} WHERE id = ?", (object_id,), fetch="one")
    if not exists:
        return None, None, "The selected responsible party could not be found."
    return dict(cause), object_id, None


def _validate_problem_form():
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    problem_type_id = _parse_id(request.form.get("problem_type_id"))
    cause_category_id = _parse_id(request.form.get("cause_category_id"))
    assigned_user_id = _parse_id(request.form.get("assigned_user_id"))
    status = request.form.get("status") or "open"

    if not title:
        return None, "Title is required."
    if not problem_type_id or not db_execute(
        "SELECT id FROM problem_types WHERE id = ? AND is_active = TRUE",
        (problem_type_id,), fetch="one"
    ):
        return None, "Choose a valid problem type."
    cause, cause_object_id, error = _cause_selection(
        cause_category_id, request.form.get("cause_object_id")
    )
    if error:
        return None, error
    if assigned_user_id and not db_execute(
        "SELECT id FROM users WHERE id = ?", (assigned_user_id,), fetch="one"
    ):
        return None, "Choose a valid owner."
    if status not in STATUS_LABELS:
        return None, "Choose a valid status."

    return {
        "title": title,
        "description": description or None,
        "problem_type_id": problem_type_id,
        "cause_category_id": cause["id"],
        "cause_object_id": cause_object_id,
        "assigned_user_id": assigned_user_id,
        "status": status,
        "customer_ids": _parse_id_list(request.form.getlist("customer_ids")),
        "supplier_ids": _parse_id_list(request.form.getlist("supplier_ids")),
    }, None


@problems_bp.route("/", methods=["GET", "POST"])
@login_required
def list_problems():
    if request.method == "POST":
        values, error = _validate_problem_form()
        if error:
            flash(error, "error")
            return redirect(url_for("problems.list_problems"))

        resolved_at = datetime.now() if values["status"] == "resolved" else None
        row = db_execute(
            """
            INSERT INTO problems (
                title, description, problem_type_id, cause_category_id,
                cause_object_id, assigned_user_id, created_by_user_id,
                status, resolved_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (
                values["title"], values["description"], values["problem_type_id"],
                values["cause_category_id"], values["cause_object_id"],
                values["assigned_user_id"], current_user.id, values["status"], resolved_at,
            ),
            fetch="one",
            commit=True,
        )
        problem_id = row["id"]
        _sync_problem_objects(problem_id, values["customer_ids"], values["supplier_ids"])
        db_execute(
            """
            INSERT INTO problem_status_history
                (problem_id, from_status, to_status, changed_by_user_id)
            VALUES (?, NULL, ?, ?)
            """,
            (problem_id, values["status"], current_user.id),
            commit=True,
        )
        flash(f"Problem #{problem_id} created.", "success")
        return redirect(url_for("problems.view_problem", problem_id=problem_id))

    status = request.args.get("status", "active")
    problem_type_id = _parse_id(request.args.get("problem_type_id"))
    cause_category_id = _parse_id(request.args.get("cause_category_id"))
    assigned_user_id = _parse_id(request.args.get("assigned_user_id"))
    customer_id = _parse_id(request.args.get("customer_id"))
    supplier_id = _parse_id(request.args.get("supplier_id"))
    query = (request.args.get("q") or "").strip()

    clauses = []
    params = []
    if status == "active":
        clauses.append("p.status != 'resolved'")
    elif status in STATUS_LABELS:
        clauses.append("p.status = ?")
        params.append(status)
    if problem_type_id:
        clauses.append("p.problem_type_id = ?")
        params.append(problem_type_id)
    if cause_category_id:
        clauses.append("p.cause_category_id = ?")
        params.append(cause_category_id)
    if assigned_user_id:
        clauses.append("p.assigned_user_id = ?")
        params.append(assigned_user_id)
    if customer_id:
        clauses.append("EXISTS (SELECT 1 FROM problem_objects po WHERE po.problem_id = p.id AND po.object_type = 'customer' AND po.object_id = ?)")
        params.append(customer_id)
    if supplier_id:
        clauses.append("EXISTS (SELECT 1 FROM problem_objects po WHERE po.problem_id = p.id AND po.object_type = 'supplier' AND po.object_id = ?)")
        params.append(supplier_id)
    if query:
        clauses.append("(p.title ILIKE ? OR p.description ILIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])
    where = "WHERE " + " AND ".join(clauses) if clauses else ""

    rows = db_execute(
        f"""
        SELECT
            p.id, p.title, p.status, p.is_demo, p.created_at, p.resolved_at,
            pt.name AS problem_type_name,
            pc.name AS cause_category_name,
            pc.party_type AS cause_party_type,
            au.username AS assigned_user_name,
            CASE
                WHEN pc.party_type = 'customer' THEN cause_customer.name
                WHEN pc.party_type = 'supplier' THEN cause_supplier.name
                WHEN pc.party_type = 'user' THEN cause_user.username
                ELSE NULL
            END AS cause_object_name,
            EXTRACT(EPOCH FROM (COALESCE(p.resolved_at, CURRENT_TIMESTAMP) - p.created_at)) / 86400.0 AS age_days
        FROM problems p
        JOIN problem_types pt ON pt.id = p.problem_type_id
        JOIN problem_cause_categories pc ON pc.id = p.cause_category_id
        LEFT JOIN users au ON au.id = p.assigned_user_id
        LEFT JOIN customers cause_customer ON pc.party_type = 'customer' AND cause_customer.id = p.cause_object_id
        LEFT JOIN suppliers cause_supplier ON pc.party_type = 'supplier' AND cause_supplier.id = p.cause_object_id
        LEFT JOIN users cause_user ON pc.party_type = 'user' AND cause_user.id = p.cause_object_id
        {where}
        ORDER BY CASE WHEN p.status = 'resolved' THEN 1 ELSE 0 END, p.created_at DESC
        LIMIT 500
        """,
        params,
        fetch="all",
    ) or []
    problems = [dict(row) for row in rows]
    objects = _fetch_objects_for_problems([problem["id"] for problem in problems])
    for problem in problems:
        problem["linked_objects"] = objects.get(problem["id"], [])

    return render_template(
        "problems/list.html",
        problems=problems,
        types=_fetch_types(active_only=True),
        causes=_fetch_causes(active_only=True),
        users=_fetch_users(),
        statuses=PROBLEM_STATUSES,
        filters={
            "status": status, "problem_type_id": problem_type_id,
            "cause_category_id": cause_category_id, "assigned_user_id": assigned_user_id,
            "customer_id": customer_id, "supplier_id": supplier_id, "q": query,
            "customer_name": _object_name("customer", customer_id),
            "supplier_name": _object_name("supplier", supplier_id),
        },
    )


def _object_name(object_type, object_id):
    if not object_id:
        return None
    table = "customers" if object_type == "customer" else "suppliers"
    row = db_execute(f"SELECT name FROM {table} WHERE id = ?", (object_id,), fetch="one")
    return row.get("name") if row else None


@problems_bp.route("/<int:problem_id>", methods=["GET"])
@login_required
def view_problem(problem_id):
    problem = _fetch_problem(problem_id)
    if not problem:
        abort(404)
    problem["linked_objects"] = _fetch_problem_objects(problem_id)
    updates = [dict(row) for row in (db_execute(
        """
        SELECT pu.*, u.username AS user_name
        FROM problem_updates pu
        JOIN users u ON u.id = pu.user_id
        WHERE pu.problem_id = ?
        ORDER BY pu.created_at DESC
        """,
        (problem_id,), fetch="all"
    ) or [])]
    history = [dict(row) for row in (db_execute(
        """
        SELECT psh.*, u.username AS changed_by_name
        FROM problem_status_history psh
        JOIN users u ON u.id = psh.changed_by_user_id
        WHERE psh.problem_id = ?
        ORDER BY psh.changed_at DESC
        """,
        (problem_id,), fetch="all"
    ) or [])]
    ticket_visibility, ticket_visibility_params = _ticket_visibility_sql("t")
    linked_tickets = [dict(row) for row in (db_execute(
        f"""
        SELECT
            t.id, t.title, t.due_date, t.priority,
            ts.name AS status_name, ts.is_closed,
            u.username AS assigned_user_name
        FROM problem_tickets pt
        JOIN tickets t ON t.id = pt.ticket_id
        JOIN ticket_statuses ts ON ts.id = t.status_id
        LEFT JOIN users u ON u.id = t.assigned_user_id
        WHERE pt.problem_id = ?
        {ticket_visibility}
        ORDER BY ts.is_closed, t.created_at DESC
        """,
        [problem_id] + ticket_visibility_params, fetch="all"
    ) or [])]
    workspaces = [dict(row) for row in (db_execute(
        """
        SELECT id, name
        FROM ticket_workspaces
        WHERE COALESCE(is_external, FALSE) = FALSE
        ORDER BY name
        """,
        fetch="all",
    ) or [])]
    return render_template(
        "problems/detail.html",
        problem=problem,
        updates=updates,
        status_history=history,
        linked_tickets=linked_tickets,
        types=_fetch_types(),
        causes=_fetch_causes(),
        users=_fetch_users(),
        statuses=PROBLEM_STATUSES,
        status_labels=STATUS_LABELS,
        workspaces=workspaces,
    )


@problems_bp.route("/<int:problem_id>/edit", methods=["POST"])
@login_required
def edit_problem(problem_id):
    problem = _fetch_problem(problem_id)
    if not problem:
        abort(404)
    values, error = _validate_problem_form()
    if error:
        flash(error, "error")
        return redirect(url_for("problems.view_problem", problem_id=problem_id))

    status_changed = values["status"] != problem["status"]
    if values["status"] == "resolved":
        resolved_at = problem.get("resolved_at") or datetime.now()
    else:
        resolved_at = None
    db_execute(
        """
        UPDATE problems
        SET title = ?, description = ?, problem_type_id = ?, cause_category_id = ?,
            cause_object_id = ?, assigned_user_id = ?, status = ?, resolved_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            values["title"], values["description"], values["problem_type_id"],
            values["cause_category_id"], values["cause_object_id"],
            values["assigned_user_id"], values["status"], resolved_at, problem_id,
        ),
        commit=True,
    )
    _sync_problem_objects(problem_id, values["customer_ids"], values["supplier_ids"])
    if status_changed:
        db_execute(
            """
            INSERT INTO problem_status_history
                (problem_id, from_status, to_status, changed_by_user_id)
            VALUES (?, ?, ?, ?)
            """,
            (problem_id, problem["status"], values["status"], current_user.id),
            commit=True,
        )
        db_execute(
            "INSERT INTO problem_updates (problem_id, user_id, update_text) VALUES (?, ?, ?)",
            (
                problem_id, current_user.id,
                f"Status changed from {STATUS_LABELS[problem['status']]} to {STATUS_LABELS[values['status']]}",
            ),
            commit=True,
        )
    flash("Problem updated.", "success")
    return redirect(url_for("problems.view_problem", problem_id=problem_id))


@problems_bp.route("/<int:problem_id>/updates", methods=["POST"])
@login_required
def add_update(problem_id):
    if not _fetch_problem(problem_id):
        abort(404)
    update_text = (request.form.get("update_text") or "").strip()
    if not update_text:
        flash("Update text is required.", "error")
    else:
        db_execute(
            "INSERT INTO problem_updates (problem_id, user_id, update_text) VALUES (?, ?, ?)",
            (problem_id, current_user.id, update_text),
            commit=True,
        )
        db_execute(
            "UPDATE problems SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (problem_id,), commit=True,
        )
    return redirect(url_for("problems.view_problem", problem_id=problem_id))


@problems_bp.route("/<int:problem_id>/tickets/link", methods=["POST"])
@login_required
def link_ticket(problem_id):
    if not _fetch_problem(problem_id):
        abort(404)
    ticket_id = _parse_id(request.form.get("ticket_id"))
    visibility, visibility_params = _ticket_visibility_sql("t")
    ticket = db_execute(
        f"SELECT t.id, t.title FROM tickets t WHERE t.id = ? {visibility}",
        [ticket_id] + visibility_params,
        fetch="one",
    ) if ticket_id else None
    if not ticket:
        flash("Ticket not found.", "error")
    else:
        linked = db_execute(
            """
            INSERT INTO problem_tickets (problem_id, ticket_id, created_by_user_id)
            VALUES (?, ?, ?)
            ON CONFLICT DO NOTHING
            RETURNING ticket_id
            """,
            (problem_id, ticket_id, current_user.id),
            fetch="one",
            commit=True,
        )
        if linked:
            db_execute(
                "INSERT INTO ticket_updates (ticket_id, user_id, update_text) VALUES (?, ?, ?)",
                (ticket_id, current_user.id, f"Linked to problem #{problem_id}: /problems/{problem_id}"),
                commit=True,
            )
            flash(f"Ticket #{ticket_id} linked.", "success")
        else:
            flash(f"Ticket #{ticket_id} is already linked.", "info")
    return redirect(url_for("problems.view_problem", problem_id=problem_id))


@problems_bp.route("/<int:problem_id>/tickets/create", methods=["POST"])
@login_required
def create_ticket(problem_id):
    problem = _fetch_problem(problem_id)
    if not problem:
        abort(404)
    title = (request.form.get("ticket_title") or "").strip()
    if not title:
        flash("Ticket title is required.", "error")
        return redirect(url_for("problems.view_problem", problem_id=problem_id))

    status = db_execute(
        "SELECT id FROM ticket_statuses WHERE is_closed = FALSE ORDER BY sort_order, id LIMIT 1",
        fetch="one",
    )
    if not status:
        flash("No open ticket status is configured.", "error")
        return redirect(url_for("problems.view_problem", problem_id=problem_id))

    assigned_user_id = _parse_id(request.form.get("ticket_assigned_user_id"))
    workspace_id = _parse_id(request.form.get("ticket_workspace_id"))
    if workspace_id and not db_execute(
        "SELECT id FROM ticket_workspaces WHERE id = ? AND COALESCE(is_external, FALSE) = FALSE",
        (workspace_id,), fetch="one"
    ):
        workspace_id = None
    if assigned_user_id and workspace_id:
        member_count = db_execute(
            "SELECT COUNT(*) AS count FROM ticket_workspace_members WHERE workspace_id = ?",
            (workspace_id,), fetch="one",
        )
        is_member = db_execute(
            "SELECT 1 FROM ticket_workspace_members WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, assigned_user_id), fetch="one",
        )
        if member_count and int(member_count.get("count") or 0) > 0 and not is_member:
            flash("Ticket owner must be a member of the selected workspace.", "error")
            return redirect(url_for("problems.view_problem", problem_id=problem_id))
    priority = request.form.get("ticket_priority") or "Medium"
    if priority not in ("Low", "Medium", "High"):
        priority = "Medium"
    due_date = request.form.get("ticket_due_date") or None
    description = (request.form.get("ticket_description") or "").strip()
    problem_url = url_for("problems.view_problem", problem_id=problem_id, _external=True)
    description = f"{description}\n\nRaised from problem #{problem_id}: {problem_url}".strip()

    row = db_execute(
        """
        INSERT INTO tickets (
            title, description, status_id, assigned_user_id, workspace_id,
            created_by_user_id, due_date, is_private, priority,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, FALSE, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        RETURNING id
        """,
        (
            title, description, status["id"], assigned_user_id, workspace_id,
            current_user.id, due_date, priority,
        ),
        fetch="one",
        commit=True,
    )
    ticket_id = row["id"]
    db_execute(
        """
        INSERT INTO problem_tickets (problem_id, ticket_id, created_by_user_id)
        VALUES (?, ?, ?)
        """,
        (problem_id, ticket_id, current_user.id),
        commit=True,
    )
    objects = _fetch_problem_objects(problem_id)
    if objects:
        db_execute(
            """
            INSERT INTO ticket_objects (ticket_id, object_type, object_id)
            VALUES (?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [(ticket_id, obj["object_type"], obj["object_id"]) for obj in objects],
            many=True,
            commit=True,
        )
    db_execute(
        "INSERT INTO problem_updates (problem_id, user_id, update_text) VALUES (?, ?, ?)",
        (problem_id, current_user.id, f"Created linked ticket #{ticket_id}: {title}"),
        commit=True,
    )
    if assigned_user_id:
        from routes.tickets import _notify_assignment
        _notify_assignment(
            ticket_id, assigned_user_id, workspace_id, title, False, None
        )
    flash(f"Ticket #{ticket_id} created and linked.", "success")
    return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))


@problems_bp.route("/<int:problem_id>/tickets/<int:ticket_id>/unlink", methods=["POST"])
@login_required
def unlink_ticket(problem_id, ticket_id):
    if not _fetch_problem(problem_id):
        abort(404)
    db_execute(
        "DELETE FROM problem_tickets WHERE problem_id = ? AND ticket_id = ?",
        (problem_id, ticket_id), commit=True,
    )
    flash(f"Ticket #{ticket_id} unlinked. The ticket itself was not deleted.", "success")
    return redirect(url_for("problems.view_problem", problem_id=problem_id))


@problems_bp.route("/overview", methods=["GET"])
@login_required
def overview():
    summary = dict(db_execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE status != 'resolved') AS open_count,
            COUNT(*) FILTER (WHERE status = 'resolved') AS resolved_count,
            COUNT(*) FILTER (WHERE is_demo = TRUE) AS demo_count,
            COUNT(*) FILTER (WHERE status != 'resolved' AND created_at < CURRENT_TIMESTAMP - INTERVAL '14 days') AS aging_count,
            AVG(EXTRACT(EPOCH FROM (resolved_at - created_at)) / 86400.0)
                FILTER (WHERE resolved_at IS NOT NULL) AS average_resolution_days,
            SUM(EXTRACT(EPOCH FROM (resolved_at - created_at)) / 86400.0)
                FILTER (WHERE resolved_at IS NOT NULL) AS total_resolution_days,
            SUM(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - created_at)) / 86400.0)
                FILTER (WHERE status != 'resolved') AS total_open_age_days,
            PERCENTILE_CONT(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (resolved_at - created_at)) / 86400.0
            ) FILTER (WHERE resolved_at IS NOT NULL) AS median_resolution_days
        FROM problems
        """,
        fetch="one",
    ) or {})
    by_cause = [dict(row) for row in (db_execute(
        """
        SELECT
            pc.name,
            COUNT(*) AS total_count,
            COUNT(*) FILTER (WHERE p.status != 'resolved') AS open_count,
            AVG(EXTRACT(EPOCH FROM (p.resolved_at - p.created_at)) / 86400.0)
                FILTER (WHERE p.resolved_at IS NOT NULL) AS average_days,
            SUM(EXTRACT(EPOCH FROM (p.resolved_at - p.created_at)) / 86400.0)
                FILTER (WHERE p.resolved_at IS NOT NULL) AS total_resolution_days,
            COALESCE(SUM(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - p.created_at)) / 86400.0)
                FILTER (WHERE p.status != 'resolved'), 0) AS open_age_days
        FROM problems p
        JOIN problem_cause_categories pc ON pc.id = p.cause_category_id
        GROUP BY pc.id, pc.name, pc.sort_order
        ORDER BY (COALESCE(SUM(EXTRACT(EPOCH FROM (p.resolved_at - p.created_at)) / 86400.0)
                    FILTER (WHERE p.resolved_at IS NOT NULL), 0)
                  + COALESCE(SUM(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - p.created_at)) / 86400.0)
                    FILTER (WHERE p.status != 'resolved'), 0)) DESC,
                 total_count DESC, pc.sort_order
        """,
        fetch="all",
    ) or [])]
    by_type = [dict(row) for row in (db_execute(
        """
        SELECT
            pt.name,
            COUNT(*) AS total_count,
            COUNT(*) FILTER (WHERE p.status != 'resolved') AS open_count,
            AVG(EXTRACT(EPOCH FROM (p.resolved_at - p.created_at)) / 86400.0)
                FILTER (WHERE p.resolved_at IS NOT NULL) AS average_days,
            SUM(EXTRACT(EPOCH FROM (p.resolved_at - p.created_at)) / 86400.0)
                FILTER (WHERE p.resolved_at IS NOT NULL) AS total_resolution_days,
            COALESCE(SUM(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - p.created_at)) / 86400.0)
                FILTER (WHERE p.status != 'resolved'), 0) AS open_age_days
        FROM problems p
        JOIN problem_types pt ON pt.id = p.problem_type_id
        GROUP BY pt.id, pt.name, pt.sort_order
        ORDER BY (COALESCE(SUM(EXTRACT(EPOCH FROM (p.resolved_at - p.created_at)) / 86400.0)
                    FILTER (WHERE p.resolved_at IS NOT NULL), 0)
                  + COALESCE(SUM(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - p.created_at)) / 86400.0)
                    FILTER (WHERE p.status != 'resolved'), 0)) DESC,
                 total_count DESC, pt.sort_order
        """,
        fetch="all",
    ) or [])]
    caused_by = [dict(row) for row in (db_execute(
        """
        SELECT
            pc.name AS cause_name,
            CASE
                WHEN pc.party_type = 'customer' THEN c.name
                WHEN pc.party_type = 'supplier' THEN s.name
                WHEN pc.party_type = 'user' THEN u.username
            END AS party_name,
            COUNT(*) AS total_count,
            COUNT(*) FILTER (WHERE p.status != 'resolved') AS open_count,
            AVG(EXTRACT(EPOCH FROM (p.resolved_at - p.created_at)) / 86400.0)
                FILTER (WHERE p.resolved_at IS NOT NULL) AS average_days,
            SUM(EXTRACT(EPOCH FROM (p.resolved_at - p.created_at)) / 86400.0)
                FILTER (WHERE p.resolved_at IS NOT NULL) AS total_resolution_days,
            COALESCE(SUM(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - p.created_at)) / 86400.0)
                FILTER (WHERE p.status != 'resolved'), 0) AS open_age_days
        FROM problems p
        JOIN problem_cause_categories pc ON pc.id = p.cause_category_id
        LEFT JOIN customers c ON pc.party_type = 'customer' AND c.id = p.cause_object_id
        LEFT JOIN suppliers s ON pc.party_type = 'supplier' AND s.id = p.cause_object_id
        LEFT JOIN users u ON pc.party_type = 'user' AND u.id = p.cause_object_id
        WHERE pc.party_type IS NOT NULL AND p.cause_object_id IS NOT NULL
        GROUP BY pc.name, pc.party_type, c.name, s.name, u.username
        ORDER BY (COALESCE(SUM(EXTRACT(EPOCH FROM (p.resolved_at - p.created_at)) / 86400.0)
                    FILTER (WHERE p.resolved_at IS NOT NULL), 0)
                  + COALESCE(SUM(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - p.created_at)) / 86400.0)
                    FILTER (WHERE p.status != 'resolved'), 0)) DESC,
                 total_count DESC, party_name
        LIMIT 20
        """,
        fetch="all",
    ) or [])]
    related_companies = [dict(row) for row in (db_execute(
        """
        SELECT
            po.object_type,
            CASE WHEN po.object_type = 'customer' THEN c.name ELSE s.name END AS company_name,
            COUNT(*) AS total_count,
            COUNT(*) FILTER (WHERE p.status != 'resolved') AS open_count,
            SUM(EXTRACT(EPOCH FROM (p.resolved_at - p.created_at)) / 86400.0)
                FILTER (WHERE p.resolved_at IS NOT NULL) AS total_resolution_days,
            COALESCE(SUM(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - p.created_at)) / 86400.0)
                FILTER (WHERE p.status != 'resolved'), 0) AS open_age_days
        FROM problem_objects po
        JOIN problems p ON p.id = po.problem_id
        LEFT JOIN customers c ON po.object_type = 'customer' AND c.id = po.object_id
        LEFT JOIN suppliers s ON po.object_type = 'supplier' AND s.id = po.object_id
        GROUP BY po.object_type, po.object_id, c.name, s.name
        ORDER BY (COALESCE(SUM(EXTRACT(EPOCH FROM (p.resolved_at - p.created_at)) / 86400.0)
                    FILTER (WHERE p.resolved_at IS NOT NULL), 0)
                  + COALESCE(SUM(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - p.created_at)) / 86400.0)
                    FILTER (WHERE p.status != 'resolved'), 0)) DESC,
                 total_count DESC, company_name
        LIMIT 20
        """,
        fetch="all",
    ) or [])]
    oldest = [dict(row) for row in (db_execute(
        """
        SELECT p.id, p.title, p.status, p.created_at,
               pt.name AS problem_type_name,
               EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - p.created_at)) / 86400.0 AS age_days
        FROM problems p
        JOIN problem_types pt ON pt.id = p.problem_type_id
        WHERE p.status != 'resolved'
        ORDER BY p.created_at
        LIMIT 10
        """,
        fetch="all",
    ) or [])]
    for row in by_cause + by_type:
        row["time_burden_days"] = float(row.get("total_resolution_days") or 0) + float(row.get("open_age_days") or 0)
    max_cause = max([row["time_burden_days"] for row in by_cause] or [1]) or 1
    max_type = max([row["time_burden_days"] for row in by_type] or [1]) or 1
    for row in by_cause:
        row["bar_percent"] = round(row["time_burden_days"] * 100 / max_cause)
    for row in by_type:
        row["bar_percent"] = round(row["time_burden_days"] * 100 / max_type)
    return render_template(
        "problems/overview.html",
        summary=summary,
        by_cause=by_cause,
        by_type=by_type,
        caused_by=caused_by,
        related_companies=related_companies,
        oldest=oldest,
        status_labels=STATUS_LABELS,
        is_admin=_is_admin(),
    )


DEMO_TITLES_BY_TYPE = {
    "Documentation": [
        "Missing trace documents", "Certificate pack incomplete",
        "Incorrect release certificate supplied", "Packing list missing from shipment",
    ],
    "Incorrect specification / revision": [
        "Customer revision requirement was unclear", "Obsolete revision quoted",
        "Specification mismatch discovered during review", "Incorrect drawing revision supplied",
    ],
    "Pricing": [
        "Incorrect price entered on quotation", "Supplier price break was missed",
        "Currency conversion applied incorrectly", "Quoted margin below agreed threshold",
    ],
    "Quantity": [
        "Incorrect quantity ordered", "Shipment quantity did not match paperwork",
        "Minimum order quantity overlooked", "Customer quantity changed after sourcing",
    ],
    "Quality": [
        "Parts failed incoming inspection", "Packaging damage found on arrival",
        "Condition did not match quotation", "Batch contained non-conforming parts",
    ],
    "Delivery": [
        "Supplier delivery missed promised date", "Carrier delay affected customer delivery",
        "Expedite request was not actioned", "Lead time changed after order placement",
    ],
    "Communication": [
        "Important email was not followed up", "Requirement changed without notification",
        "Order acknowledgement discrepancy was missed", "Handover information was incomplete",
    ],
    "Data entry": [
        "Part number entered incorrectly", "Delivery address copied incorrectly",
        "Wrong supplier selected on purchase order", "Customer reference entered incorrectly",
    ],
    "System / technical": [
        "Automated price lookup returned stale data", "Portal search omitted available stock",
        "Document upload failed", "Order status did not synchronise",
    ],
    "Other": [
        "Operational exception needs investigation", "Unexpected issue delayed completion",
        "Process gap identified during order review", "Uncategorised service issue",
    ],
}

DEMO_UPDATES = [
    "Initial details reviewed and the relevant records have been gathered.",
    "Owner contacted the parties involved and is waiting for a response.",
    "Root cause confirmed; corrective action is now in progress.",
    "Customer impact has been assessed and communicated internally.",
    "Replacement information received and checked.",
    "Corrective action completed; monitoring for recurrence.",
]


def _weighted_demo_id(ids):
    if not ids:
        return None
    # A few repeated parties make the demonstration charts show recognisable
    # hotspots while every choice still comes from a real runtime numeric ID.
    weights = [8, 5, 3] + [1] * max(0, len(ids) - 3)
    return random.choices(ids, weights=weights[:len(ids)], k=1)[0]


@problems_bp.route("/demo/seed", methods=["POST"])
@login_required
def seed_demo_data():
    if not _is_admin():
        abort(403)
    count = _parse_id(request.form.get("count")) or 60
    count = max(10, min(count, 200))
    now = datetime.now()

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT id, name FROM problem_types WHERE is_active = TRUE ORDER BY sort_order, id")
        problem_types = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT id, code, party_type FROM problem_cause_categories WHERE is_active = TRUE ORDER BY sort_order, id")
        causes = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT id FROM users ORDER BY RANDOM()")
        user_ids = [int(row["id"]) for row in cur.fetchall()]
        cur.execute("SELECT id FROM customers ORDER BY RANDOM()")
        customer_ids = [int(row["id"]) for row in cur.fetchall()]
        cur.execute("SELECT id FROM suppliers ORDER BY RANDOM()")
        supplier_ids = [int(row["id"]) for row in cur.fetchall()]

        if not problem_types or not causes or not user_ids:
            flash("Demo data requires at least one problem type, cause, and user.", "error")
            return redirect(url_for("problems.overview"))

        cause_weights = {
            "supplier_error": 28, "customer_error": 18, "user_error": 14,
            "internal_process": 16, "system_error": 9, "carrier_logistics": 7,
            "external_other": 4, "unknown": 4,
        }
        cause_choices = [cause for cause in causes]
        weights = [cause_weights.get(cause["code"], 5) for cause in cause_choices]

        for index in range(count):
            problem_type = random.choice(problem_types)
            cause = random.choices(cause_choices, weights=weights, k=1)[0]
            age_days = random.randint(2, 180)
            created_at = now - timedelta(days=age_days, hours=random.randint(0, 20))
            status = random.choices(
                ["open", "investigating", "waiting", "resolved"],
                weights=[17, 18, 10, 55], k=1,
            )[0]
            resolved_at = None
            if status == "resolved":
                duration_days = random.randint(1, max(1, min(age_days, 55)))
                resolved_at = created_at + timedelta(days=duration_days, hours=random.randint(0, 20))

            party_type = cause.get("party_type")
            if party_type == "supplier":
                cause_object_id = _weighted_demo_id(supplier_ids)
            elif party_type == "customer":
                cause_object_id = _weighted_demo_id(customer_ids)
            elif party_type == "user":
                cause_object_id = _weighted_demo_id(user_ids)
            else:
                cause_object_id = None

            titles = DEMO_TITLES_BY_TYPE.get(problem_type["name"], DEMO_TITLES_BY_TYPE["Other"])
            title = random.choice(titles)
            if index and random.random() < 0.18:
                title = f"Repeat: {title}"
            assigned_user_id = _weighted_demo_id(user_ids)
            cur.execute(
                """
                INSERT INTO problems (
                    title, description, problem_type_id, cause_category_id,
                    cause_object_id, assigned_user_id, created_by_user_id,
                    status, is_demo, created_at, updated_at, resolved_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?, ?)
                RETURNING id
                """,
                (
                    title,
                    "Demonstration record generated automatically for the problem tracker presentation.",
                    problem_type["id"], cause["id"], cause_object_id,
                    assigned_user_id, current_user.id, status, created_at,
                    resolved_at or now, resolved_at,
                ),
            )
            problem_id = cur.fetchone()["id"]

            related = set()
            if party_type in ("customer", "supplier") and cause_object_id:
                related.add((party_type, cause_object_id))
            if customer_ids and random.random() < 0.75:
                related.add(("customer", _weighted_demo_id(customer_ids)))
            if supplier_ids and random.random() < 0.8:
                related.add(("supplier", _weighted_demo_id(supplier_ids)))
            if customer_ids and random.random() < 0.12:
                related.add(("customer", random.choice(customer_ids)))
            if supplier_ids and random.random() < 0.12:
                related.add(("supplier", random.choice(supplier_ids)))
            for object_type, object_id in related:
                cur.execute(
                    "INSERT INTO problem_objects (problem_id, object_type, object_id, created_at) VALUES (?, ?, ?, ?)",
                    (problem_id, object_type, object_id, created_at),
                )

            history = [(None, "open", created_at)]
            if status in ("investigating", "waiting", "resolved"):
                investigating_at = created_at + timedelta(days=min(1, max(age_days / 4, .1)))
                history.append(("open", "investigating", investigating_at))
            if status == "waiting":
                history.append(("investigating", "waiting", created_at + timedelta(days=min(3, max(age_days / 2, .2)))))
            elif status == "resolved":
                history.append(("investigating", "resolved", resolved_at))
            for from_status, to_status, changed_at in history:
                cur.execute(
                    """
                    INSERT INTO problem_status_history
                        (problem_id, from_status, to_status, changed_by_user_id, changed_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (problem_id, from_status, to_status, random.choice(user_ids), changed_at),
                )

            update_count = random.randint(1, 3)
            end_at = resolved_at or now
            span_seconds = max(1, int((end_at - created_at).total_seconds()))
            for update_index in range(update_count):
                fraction = (update_index + 1) / (update_count + 1)
                update_at = created_at + timedelta(seconds=int(span_seconds * fraction))
                cur.execute(
                    """
                    INSERT INTO problem_updates
                        (problem_id, user_id, update_text, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (problem_id, random.choice(user_ids), random.choice(DEMO_UPDATES), update_at),
                )

    flash(f"Generated {count} demonstration problems using random runtime record IDs.", "success")
    return redirect(url_for("problems.overview"))


@problems_bp.route("/demo/nuke", methods=["POST"])
@login_required
def nuke_problem_data():
    if not _is_admin():
        abort(403)
    if (request.form.get("confirmation") or "").strip() != "NUKE ALL PROBLEMS":
        flash("Nothing was deleted. Enter the exact confirmation phrase.", "error")
        return redirect(url_for("problems.overview"))

    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM problems")
        deleted_count = cur.rowcount
    flash(
        f"Deleted {deleted_count} problem records. Existing tickets and company records were not deleted.",
        "success",
    )
    return redirect(url_for("problems.overview"))


@problems_bp.route("/demo/clear", methods=["POST"])
@login_required
def clear_demo_data():
    if not _is_admin():
        abort(403)
    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM problems WHERE is_demo = TRUE")
        deleted_count = cur.rowcount
    flash(f"Deleted {deleted_count} demonstration problems.", "success")
    return redirect(url_for("problems.overview"))


@problems_bp.route("/lookup/<object_type>", methods=["GET"])
@login_required
def lookup(object_type):
    config = {
        "customer": ("customers", "name"),
        "supplier": ("suppliers", "name"),
        "user": ("users", "username"),
    }
    if object_type not in config:
        abort(404)
    table, name_column = config[object_type]
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify([])
    rows = db_execute(
        f"""
        SELECT id, {name_column} AS name
        FROM {table}
        WHERE {name_column} ILIKE ?
        ORDER BY {name_column}
        LIMIT 12
        """,
        (f"%{query}%",),
        fetch="all",
    ) or []
    return jsonify([dict(row) for row in rows])
