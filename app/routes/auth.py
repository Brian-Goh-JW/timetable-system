from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from flask_mail import Message
from app import db, mail
from app.models.login_throttle import LoginThrottle
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User

auth_bp = Blueprint('auth', __name__)
def _login_attempt_key(email):
    raw = f'{request.remote_addr or "unknown"}|{email}'.encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _aware(value):
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _is_login_limited(key):
    row = db.session.get(LoginThrottle, key)
    now = datetime.now(timezone.utc)
    return bool(row and row.blocked_until and _aware(row.blocked_until) > now)


def _record_failed_login(key):
    now = datetime.now(timezone.utc)
    window = current_app.config.get('LOGIN_RATE_LIMIT_WINDOW', 900)
    maximum = current_app.config.get('LOGIN_RATE_LIMIT_ATTEMPTS', 5)
    row = db.session.get(LoginThrottle, key)
    if row is None:
        row = LoginThrottle(key_hash=key, failure_count=0, window_started_at=now)
        db.session.add(row)
    elif now - _aware(row.window_started_at) > timedelta(seconds=window):
        row.failure_count = 0
        row.window_started_at = now
        row.blocked_until = None
    row.failure_count += 1
    row.updated_at = now
    if row.failure_count >= maximum:
        row.blocked_until = now + timedelta(seconds=window)
    db.session.commit()


def _clear_failed_logins(key):
    row = db.session.get(LoginThrottle, key)
    if row:
        db.session.delete(row)
        db.session.commit()


def _password_error(password):
    if len(password) < 10:
        return 'Password must be at least 10 characters.'
    if not any(ch.isupper() for ch in password) or not any(ch.islower() for ch in password):
        return 'Password must contain uppercase and lowercase letters.'
    if not any(ch.isdigit() for ch in password):
        return 'Password must contain a number.'
    return None


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

        if user and user.is_active and user.check_password(password):
            _clear_failed_logins(attempt_key)
            login_user(user)
            if user.must_change_password:
                return redirect(url_for('auth.change_password'))
            return _redirect_by_role(user.role)
        else:
            _record_failed_login(attempt_key)
            flash('Incorrect email or password. Please try again.', 'danger')

    return render_template('auth/login.html')


@auth_bp.route('/account/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        error = None
        if not current_user.check_password(current_password):
            error = 'Current password is incorrect.'
        elif new_password != confirm:
            error = 'New passwords do not match.'
        else:
            error = _password_error(new_password)
        if error:
            flash(error, 'danger')
        else:
            current_user.set_password(new_password)
            current_user.must_change_password = False
            db.session.commit()
            flash('Password changed successfully.', 'success')
            return _redirect_by_role(current_user.role)
    return render_template('auth/change_password.html')


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = User.query.filter_by(email=email, active=True).first()
        if user and current_app.config.get('MAIL_USERNAME'):
            raw_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
            PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).update(
                {'used_at': datetime.now(timezone.utc)}, synchronize_session=False
            )
            db.session.add(PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            ))
            db.session.commit()
            reset_url = url_for('auth.reset_password', token=raw_token, _external=True)
            mail.send(Message(
                subject='SIT Timetable password reset',
                recipients=[user.email],
                body=f'Use this link within 30 minutes to reset your password:\n\n{reset_url}',
            ))
        flash('If an active account exists and email is configured, a reset link has been sent.', 'info')
        return redirect(url_for('auth.login'))
    return render_template('auth/forgot_password.html')


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    row = PasswordResetToken.query.filter_by(token_hash=token_hash, used_at=None).first()
    now = datetime.now(timezone.utc)
    if row is None or _aware(row.expires_at) <= now or not row.user.is_active:
        flash('That reset link is invalid or has expired.', 'danger')
        return redirect(url_for('auth.forgot_password'))
    if request.method == 'POST':
        password = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        error = _password_error(password) if password == confirm else 'New passwords do not match.'
        if error:
            flash(error, 'danger')
        else:
            row.user.set_password(password)
            row.user.must_change_password = False
            row.used_at = now
            db.session.commit()
            flash('Password reset complete. You can now sign in.', 'success')
            return redirect(url_for('auth.login'))
    return render_template('auth/reset_password.html')


@auth_bp.route('/sso/callback')
def sso_callback():
    """Login hook for a trusted reverse proxy that completed OIDC/SAML."""
    if not current_app.config.get('TRUSTED_SSO_ENABLED'):
        return 'Institutional sign-in is not configured.', 404
    supplied = request.headers.get('X-SSO-Token', '')
    expected = current_app.config.get('SSO_SHARED_SECRET', '')
    if not expected or not secrets.compare_digest(supplied, expected):
        return 'Invalid institutional authentication response.', 403
    email = request.headers.get('X-SSO-Email', '').strip().lower()
    user = User.query.filter_by(email=email, active=True).first()
    if user is None:
        return 'No active timetable account is assigned to this identity.', 403
    login_user(user)
    return _redirect_by_role(user.role)


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
