from datetime import datetime, timezone
import json
import threading

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app, Response
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
    delete_all_news_sources,
    delete_news_articles,
    delete_news_source,
    delete_news_sources,
    ingestion_stats,
    list_recent_articles,
    list_sources,
    run_ingestion,
    save_news_source,
    selection_diagnostics,
    set_source_active,
    test_source,
    update_news_source,
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
    article_view = request.args.get('articles', 'matched')
    return _render_news_control(article_view=article_view)


def _render_news_control(**context):
    article_view = context.pop('article_view', request.args.get('articles', 'matched'))
    article_view = 'all' if article_view == 'all' else 'matched'
    return render_template(
        'admin/news_control.html',
        stats=ingestion_stats(),
        sources=list_sources(),
        articles=list_recent_articles(
            limit=100,
            matched_only=article_view == 'matched',
            prioritize_selected=True,
        ),
        article_view=article_view,
        ingestion_job=NEWS_INGESTION_JOB,
        **context,
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


@admin_bp.route('/news/sources', methods=['POST'])
@admin_required
def add_news_source():
    try:
        save_news_source(
            name=request.form.get('name'),
            source_type=request.form.get('source_type'),
            url=request.form.get('url'),
            query=request.form.get('query'),
            sector_tag=request.form.get('sector_tag'),
            priority=request.form.get('priority'),
            check_frequency_minutes=request.form.get('check_frequency_minutes'),
            active=request.form.get('active') == '1',
        )
        flash('News source saved.', 'success')
    except Exception as exc:
        flash(f'Unable to save news source: {exc}', 'error')
    return redirect(url_for('admin.news_control'))


@admin_bp.route('/news/sources/<int:source_id>', methods=['POST'])
@admin_required
def update_news_source_route(source_id):
    try:
        update_news_source(
            source_id=source_id,
            name=request.form.get('name'),
            source_type=request.form.get('source_type'),
            url=request.form.get('url'),
            query=request.form.get('query'),
            sector_tag=request.form.get('sector_tag'),
            priority=request.form.get('priority'),
            check_frequency_minutes=request.form.get('check_frequency_minutes'),
            active=request.form.get('active') == '1',
        )
        flash('News source updated.', 'success')
    except Exception as exc:
        flash(f'Unable to update news source: {exc}', 'error')
    return redirect(url_for('admin.news_control'))


@admin_bp.route('/news/sources/<int:source_id>/toggle', methods=['POST'])
@admin_required
def toggle_news_source(source_id):
    active = request.form.get('active') == '1'
    set_source_active(source_id, active)
    flash('News source updated.', 'success')
    return redirect(url_for('admin.news_control'))


@admin_bp.route('/news/sources/<int:source_id>/delete', methods=['POST'])
@admin_required
def delete_news_source_route(source_id):
    try:
        if delete_news_source(source_id):
            flash('News source deleted. Previously imported articles were retained.', 'success')
        else:
            flash('News source was not found.', 'warning')
    except Exception as exc:
        flash(f'Unable to delete news source: {exc}', 'error')
    return redirect(url_for('admin.news_control'))


@admin_bp.route('/news/sources/delete', methods=['POST'])
@admin_required
def delete_news_sources_route():
    try:
        source_ids = [int(value) for value in request.form.getlist('source_ids')]
    except (TypeError, ValueError):
        flash('The source selection was invalid.', 'error')
        return redirect(url_for('admin.news_control'))

    if not source_ids:
        flash('Select at least one source to delete.', 'warning')
        return redirect(url_for('admin.news_control'))

    try:
        deleted_count = delete_news_sources(source_ids)
        flash(f'Deleted {deleted_count} news source(s). Previously imported articles were retained.', 'success')
    except Exception as exc:
        flash(f'Unable to delete news sources: {exc}', 'error')
    return redirect(url_for('admin.news_control'))


@admin_bp.route('/news/sources/delete-all', methods=['POST'])
@admin_required
def delete_all_news_sources_route():
    try:
        deleted_count = delete_all_news_sources()
        flash(f'Deleted all {deleted_count} news source(s). Previously imported articles were retained.', 'success')
    except Exception as exc:
        flash(f'Unable to delete news sources: {exc}', 'error')
    return redirect(url_for('admin.news_control'))


@admin_bp.route('/news/articles/delete', methods=['POST'])
@admin_required
def delete_news_articles_route():
    article_view = 'all' if request.form.get('article_view') == 'all' else 'matched'
    try:
        article_ids = [int(value) for value in request.form.getlist('article_ids')]
    except (TypeError, ValueError):
        flash('The article selection was invalid.', 'error')
        return redirect(url_for('admin.news_control', articles=article_view))

    if not article_ids:
        flash('Select at least one article to delete.', 'warning')
        return redirect(url_for('admin.news_control', articles=article_view))

    try:
        deleted_count = delete_news_articles(article_ids)
        flash(f'Deleted {deleted_count} news article(s).', 'success')
    except Exception as exc:
        flash(f'Unable to delete news articles: {exc}', 'error')
    return redirect(url_for('admin.news_control', articles=article_view))


@admin_bp.route('/news/export')
@admin_required
def export_news_diagnostics():
    """Download an AI-friendly snapshot of source configuration and errors."""
    sources = [dict(source) for source in list_sources()]
    stats = ingestion_stats()
    payload = {
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'purpose': 'Customer news source cleanup diagnostics',
        'summary': {
            **dict(stats),
            'configured_sources': len(sources),
            'sources_with_errors': sum(bool(source.get('last_error')) for source in sources),
        },
        'last_ingestion_job': dict(NEWS_INGESTION_JOB),
        'selection_diagnostics': selection_diagnostics(),
        'sources': sources,
    }
    filename = f"news-source-diagnostics-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    return Response(
        json.dumps(payload, indent=2, default=str),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@admin_bp.route('/news/sources/<int:source_id>/test', methods=['POST'])
@admin_required
def test_news_source_route(source_id):
    """Preview one source without importing its articles or changing last_checked_at."""
    try:
        result = test_source(source_id)
        return _render_news_control(source_test=result)
    except Exception as exc:
        return _render_news_control(source_test={'error': str(exc), 'source_id': source_id})


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
