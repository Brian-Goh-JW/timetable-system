"""
One-time schema migrations.
Adds columns and tables that were introduced after the initial db.create_all().

Run ONCE after step 2 (excel_loader):
    python bootstrap/3_migrate.py

Tables and columns affected:
    class_sessions      — adds fixed_timeslot_id (FK → timeslots)
    timetable_entries   — adds override_professor_id (FK → professors)
    timetable_flags     — adds response_deadline, notification_sent
    audit_logs          — creates the full table (via db.create_all)
    users               — updates role ENUM to include 'professor'
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db

app = create_app()

with app.app_context():

    # ------------------------------------------------------------------
    # 1. class_sessions — fixed_timeslot_id
    #    Allows the admin to pin a session to a specific recurring slot.
    # ------------------------------------------------------------------
    try:
        db.session.execute(db.text(
            'ALTER TABLE class_sessions '
            'ADD COLUMN fixed_timeslot_id INT NULL, '
            'ADD CONSTRAINT fk_cs_fixed_ts '
            'FOREIGN KEY (fixed_timeslot_id) REFERENCES timeslots(id) ON DELETE SET NULL'
        ))
        db.session.commit()
        print('[1/4] class_sessions.fixed_timeslot_id — added.')
    except Exception as e:
        if '1060' in str(e) or 'Duplicate column' in str(e):
            print('[1/4] class_sessions.fixed_timeslot_id — already exists, skipped.')
        else:
            raise

    # ------------------------------------------------------------------
    # 2. timetable_entries — override_professor_id
    #    Stores a per-week professor override set by the admin manually.
    # ------------------------------------------------------------------
    try:
        db.session.execute(db.text(
            'ALTER TABLE timetable_entries '
            'ADD COLUMN override_professor_id INT NULL, '
            'ADD CONSTRAINT fk_te_override_prof '
            'FOREIGN KEY (override_professor_id) REFERENCES professors(id) ON DELETE SET NULL'
        ))
        db.session.commit()
        print('[2/4] timetable_entries.override_professor_id — added.')
    except Exception as e:
        if '1060' in str(e) or 'Duplicate column' in str(e):
            print('[2/4] timetable_entries.override_professor_id — already exists, skipped.')
        else:
            raise

    # ------------------------------------------------------------------
    # 3. timetable_flags — response_deadline, notification_sent
    #    Tracks when the admin has notified a professor and their deadline.
    # ------------------------------------------------------------------
    try:
        db.session.execute(db.text(
            'ALTER TABLE timetable_flags '
            'ADD COLUMN response_deadline DATE NULL, '
            'ADD COLUMN notification_sent TINYINT(1) NOT NULL DEFAULT 0'
        ))
        db.session.commit()
        print('[3/4] timetable_flags.response_deadline / notification_sent — added.')
    except Exception as e:
        if '1060' in str(e) or 'Duplicate column' in str(e):
            print('[3/4] timetable_flags columns — already exist, skipped.')
        else:
            raise

    # ------------------------------------------------------------------
    # 4. audit_logs table + users.role ENUM fix
    #    Creates any missing tables and ensures role includes 'professor'.
    # ------------------------------------------------------------------
    db.create_all()
    print('[4/4] audit_logs table — created (or already present).')

    try:
        db.session.execute(db.text(
            "ALTER TABLE users MODIFY COLUMN role ENUM('admin', 'professor', 'student') NOT NULL"
        ))
        db.session.commit()
        print('[4/4] users.role ENUM — updated to include professor.')
    except Exception as e:
        print(f'[4/4] users.role ENUM — {e} (may already be correct, continuing).')

    print()
    print('All migrations complete.')
