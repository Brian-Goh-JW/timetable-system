import os
import secrets


class Config:
    # Secrets and deployment-specific values must not be committed to source
    # control. A random development key keeps local startup convenient while
    # making sessions intentionally non-persistent across restarts unless the
    # operator supplies FLASK_SECRET_KEY.
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY') or secrets.token_hex(32)
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'mysql+pymysql://root@127.0.0.1/timetable_db',
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
