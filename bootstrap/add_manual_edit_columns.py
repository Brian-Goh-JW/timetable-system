"""
One-time migration: adds override_professor_id to timetable_entries
and creates the audit_logs table.
Run once: venv/Scripts/python bootstrap/add_manual_edit_columns.py
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db

app = create_app()
with app.app_context():
    # 1. Add override_professor_id to timetable_entries
    try:
        db.session.execute(db.text(
            'ALTER TABLE timetable_entries '
            'ADD COLUMN override_professor_id INT NULL, '
            'ADD CONSTRAINT fk_te_override_prof '
            'FOREIGN KEY (override_professor_id) REFERENCES professors(id) ON DELETE SET NULL'
        ))
        db.session.commit()
        print('Done — override_professor_id added to timetable_entries.')
    except Exception as e:
        if '1060' in str(e) or 'Duplicate column' in str(e):
            print('override_professor_id already exists — skipping.')
        else:
            raise

    # 2. Create audit_logs table (new table, create_all handles it)
    db.create_all()
    print('Done — audit_logs table created (if not already present).')
