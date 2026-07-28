import os
import secrets
from sqlalchemy.engine import URL


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def _database_uri(base_dir):
    """Build a database URL without interpolating credentials into strings."""
    direct_url = os.environ.get('DATABASE_URL', '').strip()
    if direct_url:
        return direct_url

    mysql_host = os.environ.get('MYSQL_HOST', '').strip()
    if mysql_host:
        required = {
            'MYSQL_USER': os.environ.get('MYSQL_USER', '').strip(),
            'MYSQL_PASSWORD': os.environ.get('MYSQL_PASSWORD', ''),
            'MYSQL_DATABASE': os.environ.get('MYSQL_DATABASE', '').strip(),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(
                'MySQL is selected but required environment variables are missing: '
                + ', '.join(missing)
            )
        try:
            port = int(os.environ.get('MYSQL_PORT', '3306'))
        except ValueError as exc:
            raise RuntimeError('MYSQL_PORT must be a number.') from exc
        return URL.create(
            'mysql+pymysql',
            username=required['MYSQL_USER'],
            password=required['MYSQL_PASSWORD'],
            host=mysql_host,
            port=port,
            database=required['MYSQL_DATABASE'],
            query={'charset': 'utf8mb4'},
        )

    sqlite_path = os.path.join(base_dir, 'database', 'timetable.db')
    return 'sqlite:///' + sqlite_path.replace('\\', '/')


class Config:
    # Secrets and deployment-specific values must not be committed to source
    # control. A random development key keeps local startup convenient while
    # making sessions intentionally non-persistent across restarts unless the
    # operator supplies FLASK_SECRET_KEY.
    _APP_ENV = os.environ.get('APP_ENV', 'development').strip().lower()
    _ENV_SECRET = os.environ.get('FLASK_SECRET_KEY', '')
    if _APP_ENV == 'production' and not _ENV_SECRET:
        raise RuntimeError('FLASK_SECRET_KEY is required when APP_ENV=production.')
    SECRET_KEY = _ENV_SECRET or secrets.token_hex(32)
    # The database is a SQLite file kept inside the project at
    # database/timetable.db. It needs no server, no password, and travels
    # with the project folder. Set DATABASE_URL to point elsewhere (for
    # example at a MySQL server) if a different backend is ever needed.
    _BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    SQLALCHEMY_DATABASE_URI = _database_uri(_BASE_DIR)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 280,
    }

    MAX_CONTENT_LENGTH = int(os.environ.get('MAX_UPLOAD_BYTES', str(16 * 1024 * 1024)))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = _env_bool('SESSION_COOKIE_SECURE', _APP_ENV == 'production')
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    WTF_CSRF_TIME_LIMIT = 3600
    SEND_FILE_MAX_AGE_DEFAULT = 3600
    SECURITY_HEADERS_ENABLED = _env_bool('SECURITY_HEADERS_ENABLED', True)
    HSTS_ENABLED = _env_bool('HSTS_ENABLED', _APP_ENV == 'production')
    LOGIN_RATE_LIMIT_ATTEMPTS = int(os.environ.get('LOGIN_RATE_LIMIT_ATTEMPTS', '5'))
    LOGIN_RATE_LIMIT_WINDOW = int(os.environ.get('LOGIN_RATE_LIMIT_WINDOW', '900'))
    # Local development starts a lightweight worker thread after a job is
    # queued. Production should set this false and run `flask solver-worker`.
    SOLVER_RUN_IN_WEB_PROCESS = _env_bool(
        'SOLVER_RUN_IN_WEB_PROCESS', _APP_ENV != 'production'
    )
    TRUSTED_SSO_ENABLED = _env_bool('TRUSTED_SSO_ENABLED', False)
    SSO_SHARED_SECRET = os.environ.get('SSO_SHARED_SECRET', '')
    EXTERNAL_SUMMARY_ENABLED = _env_bool('EXTERNAL_SUMMARY_ENABLED', False)

    # Flask-Mail — Gmail SMTP (demo)
    # Note: SIT Microsoft 365 SMTP AUTH is disabled by institutional policy.
    # Gmail App Password used as workaround for demo purposes.
    MAIL_SERVER   = 'smtp.gmail.com'
    MAIL_PORT     = 587
    MAIL_USE_TLS  = True
    MAIL_USE_SSL  = False
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
    MAIL_TIMEOUT  = int(os.environ.get('MAIL_TIMEOUT', '15'))
    MAIL_DEFAULT_SENDER = (
        'SIT Timetable System',
        os.environ.get('MAIL_DEFAULT_SENDER', MAIL_USERNAME),
    )

    # Admin inbox — receives "cannot proceed" notifications from professors
    ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', MAIL_USERNAME)

    # Anthropic API — for timetable summary generation
    ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
