from flask import Blueprint, render_template, redirect, url_for, flash, request
from db import execute as db_execute
from models import (
    Permission,
    admin_required,
    create_user,
    get_salespeople,
    insert_salesperson,
    set_user_permissions,
)
admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


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


