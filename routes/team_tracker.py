from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from models import get_db_connection
import logging
from datetime import datetime

team_tracker_bp = Blueprint('team_tracker', __name__, url_prefix='/team-tracker')


@team_tracker_bp.route('/')
@login_required
def team_tracker_page():
    """Render the team tracker page."""
    return render_template('salespeople/team_tracker.html')


@team_tracker_bp.route('/data')
@login_required
def get_team_tracker_data():
    """
    Fetch all team tracker entries with related data.
    Returns entries grouped by salesperson for team meetings.
    """
    try:
        show_archived = request.args.get('show_archived', 'false').lower() == 'true'

        conn = get_db_connection()
        cur = conn.cursor()

        # Build query based on whether to show archived entries
        active_filter = "" if show_archived else "AND e.is_active = TRUE"

        cur.execute(f"""
            SELECT
                e.id,
                e.salesperson_id,
                s.name as salesperson_name,
                e.customer_id,
                c.name as customer_name,
                e.date_added,
                e.is_active,
                e.long_term_target,
                e.short_term_target,
                e.current_action,
                e.action_date,
                e.progress,
                e.comments,
                e.created_at,
                e.updated_at
            FROM team_tracker_entries e
            JOIN salespeople s ON e.salesperson_id = s.id
            JOIN customers c ON e.customer_id = c.id
            WHERE 1=1 {active_filter}
            ORDER BY s.name, e.date_added DESC
        """)

        entries = []
        for row in cur.fetchall():
            entry = dict(row)

            # Fetch next steps for this entry
            cur.execute("""
                SELECT
                    ns.id,
                    ns.description,
                    ns.is_completed,
                    ns.completed_at,
                    ns.completed_by,
                    u.username as completed_by_name,
                    ns.created_at
                FROM team_tracker_next_steps ns
                LEFT JOIN users u ON ns.completed_by = u.id
                WHERE ns.entry_id = ?
                ORDER BY ns.is_completed ASC, ns.position ASC, ns.created_at ASC
            """, (entry['id'],))

            entry['next_steps'] = [dict(step) for step in cur.fetchall()]

            # Format dates for JSON
            if entry['date_added']:
                entry['date_added'] = str(entry['date_added'])
            if entry['action_date']:
                entry['action_date'] = str(entry['action_date'])

            entries.append(entry)

        conn.close()

        return jsonify(success=True, entries=entries)

    except Exception as e:
        logging.exception(f"Error fetching team tracker data: {e}")
        return jsonify(success=False, error=str(e)), 500


@team_tracker_bp.route('/entry', methods=['POST'])
@login_required
def create_entry():
    """
    Add a customer to team tracker.
    Called from planner page when salesperson selects "Add to Team Tracker"
    """
    try:
        data = request.get_json()
        salesperson_id = data.get('salesperson_id')
        customer_id = data.get('customer_id')

        if not salesperson_id or not customer_id:
            return jsonify(success=False, error='salesperson_id and customer_id are required'), 400

        conn = get_db_connection()
        cur = conn.cursor()

        # Check if entry already exists (active)
        cur.execute("""
            SELECT id FROM team_tracker_entries
            WHERE salesperson_id = ? AND customer_id = ? AND is_active = TRUE
        """, (salesperson_id, customer_id))

        existing = cur.fetchone()
        if existing:
            conn.close()
            return jsonify(success=False, error='already_exists', message='Customer is already in the Team Tracker')

        # Create new entry
        cur.execute("""
            INSERT INTO team_tracker_entries (salesperson_id, customer_id, date_added)
            VALUES (?, ?, CURRENT_DATE)
            RETURNING id
        """, (salesperson_id, customer_id))

        row = cur.fetchone()
        entry_id = row['id'] if row else None

        conn.commit()
        conn.close()

        return jsonify(success=True, entry_id=entry_id)

    except Exception as e:
        logging.exception(f"Error creating team tracker entry: {e}")
        return jsonify(success=False, error=str(e)), 500


@team_tracker_bp.route('/entry/<int:entry_id>', methods=['PUT'])
@login_required
def update_entry(entry_id):
    """
    Update entry fields (targets, action, progress, comments).
    Partial updates supported - only provided fields are updated.
    """
    try:
        data = request.get_json()

        # Build dynamic update query
        allowed_fields = ['long_term_target', 'short_term_target', 'current_action',
                          'action_date', 'progress', 'comments']
        updates = []
        values = []

        for field in allowed_fields:
            if field in data:
                updates.append(f"{field} = ?")
                values.append(data[field] if data[field] != '' else None)

        if not updates:
            return jsonify(success=False, error='No fields to update'), 400

        updates.append("updated_at = CURRENT_TIMESTAMP")
        values.append(entry_id)

        conn = get_db_connection()
        cur = conn.cursor()

        query = f"UPDATE team_tracker_entries SET {', '.join(updates)} WHERE id = ?"
        cur.execute(query, tuple(values))

        conn.commit()
        conn.close()

        return jsonify(success=True)

    except Exception as e:
        logging.exception(f"Error updating team tracker entry: {e}")
        return jsonify(success=False, error=str(e)), 500


@team_tracker_bp.route('/entry/<int:entry_id>', methods=['DELETE'])
@login_required
def archive_entry(entry_id):
    """Archive (soft delete) an entry - sets is_active = FALSE"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            UPDATE team_tracker_entries
            SET is_active = FALSE, archived_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (entry_id,))

        conn.commit()
        conn.close()

        return jsonify(success=True)

    except Exception as e:
        logging.exception(f"Error archiving team tracker entry: {e}")
        return jsonify(success=False, error=str(e)), 500


@team_tracker_bp.route('/entry/<int:entry_id>/restore', methods=['POST'])
@login_required
def restore_entry(entry_id):
    """Restore a previously archived entry"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            UPDATE team_tracker_entries
            SET is_active = TRUE, archived_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (entry_id,))

        conn.commit()
        conn.close()

        return jsonify(success=True)

    except Exception as e:
        logging.exception(f"Error restoring team tracker entry: {e}")
        return jsonify(success=False, error=str(e)), 500


# ----- NEXT STEPS ROUTES -----

@team_tracker_bp.route('/entry/<int:entry_id>/steps', methods=['POST'])
@login_required
def add_next_step(entry_id):
    """Add a new next step to an entry"""
    try:
        data = request.get_json()
        description = data.get('description', '').strip()

        if not description:
            return jsonify(success=False, error='Description is required'), 400

        user_id = current_user.id if hasattr(current_user, 'id') else None

        conn = get_db_connection()
        cur = conn.cursor()

        # Get next position
        cur.execute("""
            SELECT COALESCE(MAX(position), 0) + 1 as next_pos
            FROM team_tracker_next_steps
            WHERE entry_id = ?
        """, (entry_id,))
        next_pos = cur.fetchone()['next_pos']

        cur.execute("""
            INSERT INTO team_tracker_next_steps (entry_id, description, position, created_by)
            VALUES (?, ?, ?, ?)
            RETURNING id
        """, (entry_id, description, next_pos, user_id))

        row = cur.fetchone()
        step_id = row['id'] if row else None

        conn.commit()
        conn.close()

        return jsonify(success=True, step_id=step_id)

    except Exception as e:
        logging.exception(f"Error adding next step: {e}")
        return jsonify(success=False, error=str(e)), 500


@team_tracker_bp.route('/step/<int:step_id>/toggle', methods=['POST'])
@login_required
def toggle_step(step_id):
    """Toggle step completion - sets completed_at and completed_by"""
    try:
        user_id = current_user.id if hasattr(current_user, 'id') else None

        conn = get_db_connection()
        cur = conn.cursor()

        # Get current state
        cur.execute("SELECT is_completed FROM team_tracker_next_steps WHERE id = ?", (step_id,))
        row = cur.fetchone()

        if not row:
            conn.close()
            return jsonify(success=False, error='Step not found'), 404

        is_completed = row['is_completed']

        if is_completed:
            # Uncomplete
            cur.execute("""
                UPDATE team_tracker_next_steps
                SET is_completed = FALSE, completed_at = NULL, completed_by = NULL
                WHERE id = ?
            """, (step_id,))
        else:
            # Complete
            cur.execute("""
                UPDATE team_tracker_next_steps
                SET is_completed = TRUE, completed_at = CURRENT_TIMESTAMP, completed_by = ?
                WHERE id = ?
            """, (user_id, step_id))

        conn.commit()
        conn.close()

        return jsonify(success=True, is_completed=not is_completed)

    except Exception as e:
        logging.exception(f"Error toggling step: {e}")
        return jsonify(success=False, error=str(e)), 500


@team_tracker_bp.route('/step/<int:step_id>', methods=['DELETE'])
@login_required
def delete_step(step_id):
    """Remove a next step (hard delete)"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("DELETE FROM team_tracker_next_steps WHERE id = ?", (step_id,))

        conn.commit()
        conn.close()

        return jsonify(success=True)

    except Exception as e:
        logging.exception(f"Error deleting step: {e}")
        return jsonify(success=False, error=str(e)), 500


@team_tracker_bp.route('/entry/<int:entry_id>/history')
@login_required
def get_entry_history(entry_id):
    """
    Get historical next steps for retrospective view.
    Returns all steps (completed and incomplete) ordered by created_at
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT
                ns.id,
                ns.description,
                ns.is_completed,
                ns.completed_at,
                ns.completed_by,
                u.username as completed_by_name,
                ns.created_at,
                cu.username as created_by_name
            FROM team_tracker_next_steps ns
            LEFT JOIN users u ON ns.completed_by = u.id
            LEFT JOIN users cu ON ns.created_by = cu.id
            WHERE ns.entry_id = ?
            ORDER BY ns.created_at ASC
        """, (entry_id,))

        steps = []
        for row in cur.fetchall():
            step = dict(row)
            if step['completed_at']:
                step['completed_at'] = str(step['completed_at'])
            if step['created_at']:
                step['created_at'] = str(step['created_at'])
            steps.append(step)

        conn.close()

        return jsonify(success=True, steps=steps)

    except Exception as e:
        logging.exception(f"Error fetching entry history: {e}")
        return jsonify(success=False, error=str(e)), 500
