from flask import Blueprint, render_template, redirect, url_for, flash, request
from functools import wraps
from db import execute as db_execute
from models import Permission, admin_required, permission_required, set_user_permissions, calculate_ship_dates_for_open_orders
from flask_login import current_user

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


@admin_bp.route('/users')
@admin_required
def users():
    users = db_execute('''
        SELECT users.*, user_permissions.permissions 
        FROM users 
        LEFT JOIN user_permissions ON users.id = user_permissions.user_id
    ''', fetch='all') or []
    return render_template('admin/users.html', users=users, Permission=Permission)


