import os
import secrets


class Config:
    # Secrets and deployment-specific values must not be committed to source
    # control. A random development key keeps local startup convenient while
    # making sessions intentionally non-persistent across restarts unless the
    # operator supplies FLASK_SECRET_KEY.
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY') or secrets.token_hex(32)
    # The database is a SQLite file kept inside the project at
    # database/timetable.db. It needs no server, no password, and travels
    # with the project folder. Set DATABASE_URL to point elsewhere (for
    # example at a MySQL server) if a different backend is ever needed.
    _BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    _SQLITE_PATH = os.path.join(_BASE_DIR, 'database', 'timetable.db')
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'sqlite:///' + _SQLITE_PATH.replace('\\', '/'),
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
    }

    # Flask-Mail — Gmail SMTP (demo)
    # Note: SIT Microsoft 365 SMTP AUTH is disabled by institutional policy.
    # Gmail App Password used as workaround for demo purposes.
    MAIL_SERVER   = 'smtp.gmail.com'
    MAIL_PORT     = 587
    MAIL_USE_TLS  = True
    MAIL_USE_SSL  = False
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = (
        'SIT Timetable System',
        os.environ.get('MAIL_DEFAULT_SENDER', MAIL_USERNAME),
    )

    # Admin inbox — receives "cannot proceed" notifications from professors
    ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', MAIL_USERNAME)

    # Anthropic API — for timetable summary generation
    ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
