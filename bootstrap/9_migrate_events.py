"""
Migration: create the events table.

Run once:
    python bootstrap/9_migrate_events.py
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import create_app, db

app = create_app()
with app.app_context():
    db.create_all()
    print('Events table created (or already exists).')
