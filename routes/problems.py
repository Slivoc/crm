from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from db import execute as db_execute
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
            p.id, p.title, p.status, p.created_at, p.resolved_at,
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
            COUNT(*) FILTER (WHERE status != 'resolved' AND created_at < CURRENT_TIMESTAMP - INTERVAL '14 days') AS aging_count,
            AVG(EXTRACT(EPOCH FROM (resolved_at - created_at)) / 86400.0)
                FILTER (WHERE resolved_at IS NOT NULL) AS average_resolution_days,
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
                FILTER (WHERE p.resolved_at IS NOT NULL) AS average_days
        FROM problems p
        JOIN problem_cause_categories pc ON pc.id = p.cause_category_id
        GROUP BY pc.id, pc.name, pc.sort_order
        ORDER BY total_count DESC, pc.sort_order
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
                FILTER (WHERE p.resolved_at IS NOT NULL) AS average_days
        FROM problems p
        JOIN problem_types pt ON pt.id = p.problem_type_id
        GROUP BY pt.id, pt.name, pt.sort_order
        ORDER BY total_count DESC, pt.sort_order
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
            COUNT(*) FILTER (WHERE p.status != 'resolved') AS open_count
        FROM problems p
        JOIN problem_cause_categories pc ON pc.id = p.cause_category_id
        LEFT JOIN customers c ON pc.party_type = 'customer' AND c.id = p.cause_object_id
        LEFT JOIN suppliers s ON pc.party_type = 'supplier' AND s.id = p.cause_object_id
        LEFT JOIN users u ON pc.party_type = 'user' AND u.id = p.cause_object_id
        WHERE pc.party_type IS NOT NULL AND p.cause_object_id IS NOT NULL
        GROUP BY pc.name, pc.party_type, c.name, s.name, u.username
        ORDER BY total_count DESC, party_name
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
            COUNT(*) FILTER (WHERE p.status != 'resolved') AS open_count
        FROM problem_objects po
        JOIN problems p ON p.id = po.problem_id
        LEFT JOIN customers c ON po.object_type = 'customer' AND c.id = po.object_id
        LEFT JOIN suppliers s ON po.object_type = 'supplier' AND s.id = po.object_id
        GROUP BY po.object_type, po.object_id, c.name, s.name
        ORDER BY total_count DESC, company_name
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
    max_cause = max([int(row["total_count"]) for row in by_cause] or [1])
    max_type = max([int(row["total_count"]) for row in by_type] or [1])
    for row in by_cause:
        row["bar_percent"] = round(int(row["total_count"]) * 100 / max_cause)
    for row in by_type:
        row["bar_percent"] = round(int(row["total_count"]) * 100 / max_type)
    return render_template(
        "problems/overview.html",
        summary=summary,
        by_cause=by_cause,
        by_type=by_type,
        caused_by=caused_by,
        related_companies=related_companies,
        oldest=oldest,
        status_labels=STATUS_LABELS,
    )


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
