from datetime import datetime
import threading

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app
from db import execute as db_execute
from models import (
    Permission,
    admin_required,
    create_user,
    get_salespeople,
    insert_salesperson,
    set_user_permissions,
)
from services.customer_news_ingestion import (
    ensure_seed_news_sources,
    ingestion_stats,
    list_recent_articles,
    list_sources,
    run_ingestion,
    set_source_active,
)
admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

NEWS_INGESTION_JOB = {
    'running': False,
    'started_at': None,
    'finished_at': None,
    'source_type': None,
    'result': None,
    'error': None,
}
NEWS_INGESTION_LOCK = threading.Lock()


@admin_bp.route('/users')
@admin_required
def users():
    users = db_execute('''
        SELECT users.*, user_permissions.permissions 
        FROM users 
        LEFT JOIN user_permissions ON users.id = user_permissions.user_id
    ''', fetch='all') or []
    salespeople = get_salespeople()
    return render_template(
        'admin/users.html',
        users=users,
        Permission=Permission,
        salespeople=salespeople,
    )


@admin_bp.route('/users/<int:user_id>/permissions', methods=['POST'])
@admin_required
def update_permissions(user_id):
    """Update the stored permission flags for a user."""
    permission_map = {
        'read': Permission.READ,
        'write': Permission.WRITE,
        'admin': Permission.ADMIN,
        'view_customers': Permission.VIEW_CUSTOMERS,
        'edit_customers': Permission.EDIT_CUSTOMERS,
    }
    permissions = 0
    for field, flag in permission_map.items():
        if request.form.get(field):
            permissions |= flag
    set_user_permissions(user_id, permissions)
    flash('Permissions updated.', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/create', methods=['POST'])
@admin_required
def create_user_route():
    """Create a new user and link it to a salesperson if provided."""
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    salesperson_id = request.form.get('salesperson_id')
    salesperson_name = request.form.get('salesperson_name', '').strip()

    if not username or not password:
        flash('Username and password are required.', 'error')
        return redirect(url_for('admin.users'))

    linked_salesperson_id = None
    if salesperson_name:
        try:
            linked_salesperson_id = insert_salesperson(salesperson_name)
        except Exception as exc:
            flash(f'Failed to create salesperson: {exc}', 'error')
            return redirect(url_for('admin.users'))
    elif salesperson_id:
        try:
            linked_salesperson_id = int(salesperson_id)
        except (TypeError, ValueError):
            linked_salesperson_id = None

    try:
        create_user(username, password, salesperson_id=linked_salesperson_id)
        flash('User created and linked successfully.', 'success')
    except Exception as exc:
        flash(f'Unable to create user: {exc}', 'error')

    return redirect(url_for('admin.users'))


@admin_bp.route('/news')
@admin_required
def news_control():
    ensure_seed_news_sources()
    return render_template(
        'admin/news_control.html',
        stats=ingestion_stats(),
        sources=list_sources(),
        articles=list_recent_articles(limit=100),
        ingestion_job=NEWS_INGESTION_JOB,
    )


@admin_bp.route('/news/run', methods=['POST'])
@admin_required
def run_news_ingestion_route():
    payload = request.get_json(silent=True) if request.is_json else {}
    source_type = (payload or {}).get('source_type') or request.form.get('source_type') or None
    if source_type == 'all':
        source_type = None
    started = _start_news_ingestion_background(source_type=source_type)
    if request.headers.get('Accept') == 'application/json' or request.is_json:
        return jsonify({'success': True, 'started': started, 'job': NEWS_INGESTION_JOB})
    if started:
        flash('News ingestion started in the background. Refresh this page to see progress.', 'success')
    else:
        flash('News ingestion is already running.', 'warning')
    return redirect(url_for('admin.news_control'))


@admin_bp.route('/news/sources/<int:source_id>/toggle', methods=['POST'])
@admin_required
def toggle_news_source(source_id):
    active = request.form.get('active') == '1'
    set_source_active(source_id, active)
    flash('News source updated.', 'success')
    return redirect(url_for('admin.news_control'))


def _start_news_ingestion_background(source_type=None):
    with NEWS_INGESTION_LOCK:
        if NEWS_INGESTION_JOB.get('running'):
            return False
        NEWS_INGESTION_JOB.update({
            'running': True,
            'started_at': datetime.now().isoformat(timespec='seconds'),
            'finished_at': None,
            'source_type': source_type or 'all',
            'result': None,
            'error': None,
        })

    app = current_app._get_current_object()

    def worker():
        try:
            with app.app_context():
                result = run_ingestion(source_type=source_type, limit=50)
            with NEWS_INGESTION_LOCK:
                NEWS_INGESTION_JOB.update({
                    'running': False,
                    'finished_at': datetime.now().isoformat(timespec='seconds'),
                    'result': result,
                    'error': None,
                })
        except Exception as exc:
            with NEWS_INGESTION_LOCK:
                NEWS_INGESTION_JOB.update({
                    'running': False,
                    'finished_at': datetime.now().isoformat(timespec='seconds'),
                    'result': None,
                    'error': str(exc),
                })

    threading.Thread(target=worker, name='news-ingestion-admin', daemon=True).start()
    return True


