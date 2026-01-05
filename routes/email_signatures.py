# email_signatures.py - Updated with user management

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, send_from_directory
from flask_login import current_user
from db import execute as db_execute, db_cursor
from datetime import datetime
from werkzeug.utils import secure_filename
import os
import uuid

signatures_bp = Blueprint('signatures', __name__)

ALLOWED_SIGNATURE_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}


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


def _get_signature_upload_folder():
    uploads_root = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    signatures_folder = os.path.join(current_app.root_path, uploads_root, 'signatures')
    os.makedirs(signatures_folder, exist_ok=True)
    return signatures_folder


def _list_signature_images():
    folder = _get_signature_upload_folder()
    images = []
    for entry in os.scandir(folder):
        if not entry.is_file():
            continue
        _, ext = os.path.splitext(entry.name)
        if ext.lower() not in ALLOWED_SIGNATURE_IMAGE_EXTENSIONS:
            continue

        stat = entry.stat()
        images.append({
            'filename': entry.name,
            'url': url_for('signatures.signature_image', filename=entry.name, _external=True),
            'uploaded_at': datetime.fromtimestamp(stat.st_mtime),
            'size_kb': round(stat.st_size / 1024, 1)
        })

    images.sort(key=lambda img: img['uploaded_at'], reverse=True)
    return images


def get_current_user_id():
    """Get current user ID - replace this with your actual user system"""
    if current_user and getattr(current_user, "is_authenticated", False):
        return getattr(current_user, "id", None)
    return session.get('user_id')


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

    signature = None
    if user_id is not None:
        signature = _serialize_signature(db_execute("""
            SELECT * FROM email_signatures 
            WHERE user_id = ? AND is_default = TRUE
            LIMIT 1
        """, (user_id,), fetch='one'))

    if signature:
        return signature

    return _serialize_signature(db_execute("""
        SELECT * FROM email_signatures 
        WHERE user_id IS NULL AND is_default = TRUE
        LIMIT 1
    """, fetch='one'))


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
    signature_images = _list_signature_images()
    return render_template('signatures.html', signatures=signatures, signature_images=signature_images)


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


@signatures_bp.route('/images/upload', methods=['POST'])
def upload_signature_image():
    """Upload an image for use in email signatures"""
    file = request.files.get('signature_image')

    if not file or not file.filename:
        flash('Please choose an image to upload.', 'error')
        return redirect(url_for('signatures.manage_signatures'))

    filename = secure_filename(file.filename)
    _, ext = os.path.splitext(filename)
    if ext.lower() not in ALLOWED_SIGNATURE_IMAGE_EXTENSIONS:
        allowed = ', '.join(sorted(ALLOWED_SIGNATURE_IMAGE_EXTENSIONS))
        flash(f'Invalid file type. Allowed types: {allowed}', 'error')
        return redirect(url_for('signatures.manage_signatures'))

    unique_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}{ext.lower()}"

    try:
        save_path = os.path.join(_get_signature_upload_folder(), unique_name)
        file.save(save_path)
        flash('Image uploaded successfully. Use the image tools below to insert it into your signature.', 'success')
    except Exception as e:
        flash(f'Error uploading image: {str(e)}', 'error')

    return redirect(url_for('signatures.manage_signatures'))


@signatures_bp.route('/images/<path:filename>')
def signature_image(filename):
    """Serve images stored for email signatures"""
    return send_from_directory(_get_signature_upload_folder(), filename)
