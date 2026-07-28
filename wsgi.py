"""Production WSGI entry point (for example: waitress-serve wsgi:app)."""

from app import create_app

app = create_app()
