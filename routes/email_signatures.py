# email_signatures.py - Updated with user management

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from db import execute as db_execute, db_cursor
from datetime import datetime
import os

signatures_bp = Blueprint('signatures', __name__)


def _using_postgres() -> bool:
    return os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://'))


def _execute_with_cursor(cur, query, params=None):
    prepared = query.replace('?', '%s') if _using_postgres() else query
    cur.execute(prepared, params or [])
    return cur


def _serialize_signature(row):
    if not row:
        return None
    data = dict(row)
    return {
        'id': data.get('id'),
        'name': data.get('name'),
        'signature_html': data.get('signature_html'),
        'created_at': data.get('created_at'),
        'user_id': data.get('user_id'),
        'is_default': data.get('is_default', False)
    }


def get_current_user_id():
    """Get current user ID - replace this with your actual user system"""
    return session.get('user_id', 1)  # Default to user 1 if no session


def get_email_signature_by_id(signature_id):
    """Get email signature by ID"""
    return _serialize_signature(db_execute(
        "SELECT * FROM email_signatures WHERE id = ?",
        (signature_id,),
        fetch='one'
    ))


def get_user_default_signature(user_id=None):
    """Get the default signature for a user"""
    if user_id is None:
        user_id = get_current_user_id()

    return _serialize_signature(db_execute("""
        SELECT * FROM email_signatures 
        WHERE user_id = ? AND is_default = TRUE
        LIMIT 1
    """, (user_id,), fetch='one'))


def get_all_email_signatures(user_id=None):
    """Get all email signatures for a user"""
    if user_id is None:
        user_id = get_current_user_id()

    return [dict(row) for row in db_execute("""
        SELECT * FROM email_signatures 
        WHERE user_id = ? OR user_id IS NULL
        ORDER BY is_default DESC, created_at DESC
    """, (user_id,), fetch='all')]


def set_default_signature(signature_id, user_id=None):
    """Set a signature as default for a user"""
    if user_id is None:
        user_id = get_current_user_id()

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, """
                UPDATE email_signatures 
                SET is_default = FALSE 
                WHERE user_id = ? AND is_default = TRUE
            """, (user_id,))

            _execute_with_cursor(cur, """
                UPDATE email_signatures 
                SET is_default = TRUE 
                WHERE id = ? AND user_id = ?
            """, (signature_id, user_id))

            return cur.rowcount > 0
    except Exception as e:
        print(f"Error setting default signature: {e}")
        return False


@signatures_bp.route('/')
def manage_signatures():
    """Single page to manage all signatures"""
    user_id = get_current_user_id()
    signatures = get_all_email_signatures(user_id)
    return render_template('signatures.html', signatures=signatures)


@signatures_bp.route('/save', methods=['POST'])
def save_signature():
    """Save or update email signature"""
    name = request.form.get('name', '').strip()
    signature_html = request.form.get('signature_html', '').strip()
    signature_id = request.form.get('signature_id')
    is_default = request.form.get('is_default') == 'on'
    user_id = get_current_user_id()

    if not name:
        flash('Signature name is required', 'error')
        return redirect(url_for('signatures.manage_signatures'))

    if not signature_html:
        flash('Signature HTML is required', 'error')
        return redirect(url_for('signatures.manage_signatures'))

    try:
        if signature_id:
            db_execute("""
                UPDATE email_signatures 
                SET name = ?, signature_html = ?, user_id = ?
                WHERE id = ?
            """, (name, signature_html, user_id, signature_id), commit=True)
            flash('Signature updated successfully', 'success')
        else:
            with db_cursor(commit=True) as cur:
                _execute_with_cursor(cur, """
                    INSERT INTO email_signatures (name, signature_html, user_id, created_at)
                    VALUES (?, ?, ?, ?)
                """, (name, signature_html, user_id, datetime.now()))
                signature_id = getattr(cur, 'lastrowid', None)
            flash('Signature created successfully', 'success')

        if is_default and signature_id:
            set_default_signature(signature_id, user_id)
    except Exception as e:
        flash(f'Error saving signature: {str(e)}', 'error')

    return redirect(url_for('signatures.manage_signatures'))


@signatures_bp.route('/<int:signature_id>/set-default', methods=['POST'])
def set_signature_default(signature_id):
    """Set a signature as default"""
    user_id = get_current_user_id()

    if set_default_signature(signature_id, user_id):
        flash('Default signature updated successfully', 'success')
    else:
        flash('Error setting default signature', 'error')

    return redirect(url_for('signatures.manage_signatures'))


@signatures_bp.route('/<int:signature_id>/preview')
def preview_signature(signature_id):
    """Preview email signature"""
    signature = get_email_signature_by_id(signature_id)
    if not signature:
        return "Signature not found", 404

    # Return just the signature HTML for embedding
    return signature['signature_html']


@signatures_bp.route('/default/preview')
def preview_default_signature():
    """Preview the current user's default signature"""
    user_id = get_current_user_id()
    signature = get_user_default_signature(user_id)

    if not signature:
        return "No default signature set", 404

    return signature['signature_html']


@signatures_bp.route('/<int:signature_id>/delete', methods=['POST'])
def delete_signature(signature_id):
    """Delete email signature"""
    user_id = get_current_user_id()

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, """
                DELETE FROM email_signatures 
                WHERE id = ? AND user_id = ?
            """, (signature_id, user_id))

            if cur.rowcount > 0:
                flash('Signature deleted successfully', 'success')
            else:
                flash('Signature not found or access denied', 'error')
    except Exception as e:
        flash(f'Error deleting signature: {str(e)}', 'error')

    return redirect(url_for('signatures.manage_signatures'))
