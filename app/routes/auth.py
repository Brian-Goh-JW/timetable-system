from collections import defaultdict, deque
from threading import Lock
import time

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from app.models.user import User

auth_bp = Blueprint('auth', __name__)
_failed_logins = defaultdict(deque)
_failed_logins_lock = Lock()


def _login_attempt_key(email):
    return (request.remote_addr or 'unknown', email)


def _is_login_limited(key):
    now = time.monotonic()
    window = current_app.config.get('LOGIN_RATE_LIMIT_WINDOW', 900)
    maximum = current_app.config.get('LOGIN_RATE_LIMIT_ATTEMPTS', 5)
    with _failed_logins_lock:
        attempts = _failed_logins[key]
        while attempts and now - attempts[0] > window:
            attempts.popleft()
        return len(attempts) >= maximum


def _record_failed_login(key):
    with _failed_logins_lock:
        _failed_logins[key].append(time.monotonic())


def _clear_failed_logins(key):
    with _failed_logins_lock:
        _failed_logins.pop(key, None)


@auth_bp.route('/')
def index():
    """Redirect root URL to login (or dashboard if already logged in)."""
    if current_user.is_authenticated:
        return _redirect_by_role(current_user.role)
    return redirect(url_for('auth.login'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    # If already logged in, send straight to the right dashboard
    if current_user.is_authenticated:
        return _redirect_by_role(current_user.role)

    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        attempt_key = _login_attempt_key(email)

        if _is_login_limited(attempt_key):
            flash('Too many unsuccessful sign-in attempts. Please try again later.', 'danger')
            return render_template('auth/login.html'), 429

        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            _clear_failed_logins(attempt_key)
            login_user(user)
            return _redirect_by_role(user.role)
        else:
            _record_failed_login(attempt_key)
            flash('Incorrect email or password. Please try again.', 'danger')

    return render_template('auth/login.html')


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


def _redirect_by_role(role):
    """Send the user to their role-specific dashboard."""
    if role == 'admin':
        return redirect(url_for('admin.dashboard'))
    elif role == 'professor':
        return redirect(url_for('teacher.dashboard'))
    else:
        return redirect(url_for('student.dashboard'))
