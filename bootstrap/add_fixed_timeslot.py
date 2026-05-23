"""
One-time migration: adds fixed_timeslot_id column to class_sessions.
Run once: venv/Scripts/python bootstrap/add_fixed_timeslot.py
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db

app = create_app()
with app.app_context():
    try:
        db.session.execute(db.text(
            'ALTER TABLE class_sessions '
            'ADD COLUMN fixed_timeslot_id INT NULL, '
            'ADD CONSTRAINT fk_cs_fixed_ts '
            'FOREIGN KEY (fixed_timeslot_id) REFERENCES timeslots(id) ON DELETE SET NULL'
        ))
        db.session.commit()
        print('Done — fixed_timeslot_id column added to class_sessions.')
    except Exception as e:
        if '1060' in str(e) or 'Duplicate column' in str(e):
            print('Column already exists — nothing to do.')
        else:
            raise
