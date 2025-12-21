# auth.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import LoginManager, login_user, logout_user, current_user, login_required
from models import User, get_user_by_username, create_user
from urllib.parse import urlparse, urljoin

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        try:
            print(f"DEBUG - Login attempt for username: {username}")
            user = get_user_by_username(username)
            print(f"DEBUG - Login - User found with type: {user.user_type if user else None}")

            if user and user.check_password(password):
                login_user(user)
                print(f"DEBUG - Login - After login_user, type: {current_user.user_type}")
                next_page = request.args.get('next')
                if not next_page or not is_safe_url(next_page):
                    next_page = url_for('index')
                return redirect(next_page)

            flash('Invalid username or password')
            return redirect(url_for('auth.login'))

        except Exception as e:
            print(f"DEBUG - Login error: {str(e)}")
            flash(f'Error during login: {str(e)}')
            return redirect(url_for('auth.login'))

    return render_template('auth/login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        salesperson_id = request.form.get('salesperson_id')  # This will be None if no selection made

        try:
            # Check if user already exists
            if get_user_by_username(username):
                flash('Username already exists')
                return redirect(url_for('auth.register'))

            # Create the user with optional salesperson link
            user_id = create_user(username, password, salesperson_id)

            # Log the user in automatically
            user = User.get(user_id)
            login_user(user)

            flash('Registration successful!')
            return redirect(url_for('index'))

        except Exception as e:
            flash(f'Registration error: {str(e)}')
            return redirect(url_for('auth.register'))

    return render_template('auth/register.html')

@auth_bp.route('/logout')
def logout():
    logout_user()
    # Clear the salesperson selection from session
    session.pop('selected_salesperson_id', None)
    flash('You have been logged out.')
    return redirect(url_for('index'))