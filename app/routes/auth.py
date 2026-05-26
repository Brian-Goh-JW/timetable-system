from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app.models.user import User

auth_bp = Blueprint('auth', __name__)


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

        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            login_user(user)
            return _redirect_by_role(user.role)
        else:
            flash('Incorrect email or password. Please try again.', 'danger')

    return render_template('auth/login.html')


@auth_bp.route('/logout')
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
