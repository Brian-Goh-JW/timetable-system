"""
STEP 5 — Add student_group_id column to the users table.
Enables the system to auto-load each student's timetable based on their assigned group.

Run ONCE after step 3 (3_migrate.py):
    python bootstrap/5_migrate_students.py

Tables and columns affected:
    users — adds student_group_id (FK → student_groups, ON DELETE SET NULL)
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db

app = create_app()

with app.app_context():
    try:
        db.session.execute(db.text(
            'ALTER TABLE users '
            'ADD COLUMN student_group_id INT NULL, '
            'ADD CONSTRAINT fk_users_student_group '
            'FOREIGN KEY (student_group_id) REFERENCES student_groups(id) ON DELETE SET NULL'
        ))
        db.session.commit()
        print('[1/1] users.student_group_id — added.')
    except Exception as e:
        if '1060' in str(e) or 'Duplicate column' in str(e):
            print('[1/1] users.student_group_id — already exists, skipped.')
        else:
            raise

    print()
    print('Migration complete.')
    print('You can now assign students to groups via Admin → Students.')
