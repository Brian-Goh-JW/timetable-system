"""
One-time migration: adds response_deadline and notification_sent to timetable_flags.
Run once: venv/Scripts/python bootstrap/add_flag_deadline.py
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db

app = create_app()
with app.app_context():
    try:
        db.session.execute(db.text(
            'ALTER TABLE timetable_flags '
            'ADD COLUMN response_deadline DATE NULL, '
            'ADD COLUMN notification_sent TINYINT(1) NOT NULL DEFAULT 0'
        ))
        db.session.commit()
        print('Done — response_deadline and notification_sent added to timetable_flags.')
    except Exception as e:
        if '1060' in str(e) or 'Duplicate column' in str(e):
            print('Columns already exist — nothing to do.')
        else:
            raise
