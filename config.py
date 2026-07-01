class Config:
    SECRET_KEY = 'sit-dsc2204-timetable-secret'
    SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://root:Thebunn1e5@127.0.0.1/timetable_db'
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
    MAIL_USERNAME = 'braingohjw@gmail.com'
    MAIL_PASSWORD = 'eaoqznirnncowdqf'
    MAIL_DEFAULT_SENDER = ('SIT Timetable System', 'braingohjw@gmail.com')

    # Admin inbox — receives "cannot proceed" notifications from professors
    ADMIN_EMAIL = 'braingohjw@gmail.com'

    # Anthropic API — for timetable summary generation
    ANTHROPIC_API_KEY = ''  # fill in from console.anthropic.com
