import sqlite3

from flask import Flask, flash, redirect, request, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from sqlalchemy import event
from sqlalchemy.engine import Engine
from config import Config


@event.listens_for(Engine, 'connect')
def _enforce_sqlite_foreign_keys(dbapi_connection, connection_record):
    """SQLite ignores foreign keys unless asked to enforce them per connection.

    The schema relies on foreign keys to reject a session that references a
    professor, room, or student group that does not exist, so enforcement is
    switched on for every SQLite connection. Other backends are unaffected.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()


db           = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
mail = Mail()
csrf = CSRFProtect()

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)
    csrf.init_app(app)

    from app import models  # noqa: F401 - ensures all models are registered with SQLAlchemy

    from app.routes.auth import auth_bp
    from app.routes.admin import admin_bp
    from app.routes.teacher import teacher_bp
    from app.routes.student import student_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(student_bp)

    @app.after_request
    def apply_security_headers(response):
        if not app.config.get('SECURITY_HEADERS_ENABLED', True):
            return response
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault(
            'Permissions-Policy',
            'camera=(), microphone=(), geolocation=(), payment=()',
        )
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
            "form-action 'self'; object-src 'none'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
            "https://fonts.googleapis.com; font-src 'self' data: "
            "https://cdn.jsdelivr.net https://fonts.gstatic.com; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "connect-src 'self'",
        )
        if response.mimetype == 'text/html':
            response.headers.setdefault('Cache-Control', 'no-store')
        if app.config.get('HSTS_ENABLED') and request.is_secure:
            response.headers.setdefault(
                'Strict-Transport-Security', 'max-age=31536000; includeSubDomains'
            )
        return response

    @app.errorhandler(413)
    def upload_too_large(error):
        return 'Uploaded file is too large. The limit is 16 MB.', 413

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        """Recover cleanly when a login form predates a local server reload.

        Flask signs the CSRF value with the browser session. During local
        development, restarting the app invalidates a login page that was
        already open. Sending the user to a newly signed login form is both
        safe and much clearer than exposing Flask-WTF's raw 400 page.
        """
        if request.endpoint == 'auth.login':
            flash('Your login page expired after the server reloaded. Please sign in again.',
                  'warning')
            return redirect(url_for('auth.login'), code=303)
        return error.description, 400

    @app.context_processor
    def inject_nav_open_flags_count():
        from flask_login import current_user
        if current_user.is_authenticated and current_user.role == 'admin':
            from app.models.timetable_flag import TimetableFlag
            return {'nav_open_flags_count': TimetableFlag.query.filter_by(status='open').count()}
        return {'nav_open_flags_count': 0}

    return app
