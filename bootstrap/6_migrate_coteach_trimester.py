"""
Migration: add professor_id_2 + trimester to class_sessions,
           add trimester to courses,
           drop unique constraint on courses.module_code.

Run once:
    python bootstrap/6_migrate_coteach_trimester.py
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import create_app, db

app = create_app()

with app.app_context():

    # 1. class_sessions — professor_id_2
    try:
        db.session.execute(db.text(
            'ALTER TABLE class_sessions '
            'ADD COLUMN professor_id_2 INT NULL, '
            'ADD CONSTRAINT fk_cs_prof2 '
            'FOREIGN KEY (professor_id_2) REFERENCES professors(id) ON DELETE SET NULL'
        ))
        db.session.commit()
        print('[1/4] class_sessions.professor_id_2 — added.')
    except Exception as e:
        db.session.rollback()
        print(f'[1/4] class_sessions.professor_id_2 — skipped ({e})')

    # 2. class_sessions — trimester
    try:
        db.session.execute(db.text(
            'ALTER TABLE class_sessions ADD COLUMN trimester INT NULL'
        ))
        db.session.commit()
        print('[2/4] class_sessions.trimester — added.')
    except Exception as e:
        db.session.rollback()
        print(f'[2/4] class_sessions.trimester — skipped ({e})')

    # 3. courses — trimester
    try:
        db.session.execute(db.text(
            'ALTER TABLE courses ADD COLUMN trimester INT NULL'
        ))
        db.session.commit()
        print('[3/4] courses.trimester — added.')
    except Exception as e:
        db.session.rollback()
        print(f'[3/4] courses.trimester — skipped ({e})')

    # 4. courses — drop unique constraint on module_code
    try:
        # Find and drop the unique index on module_code
        db.session.execute(db.text(
            'ALTER TABLE courses DROP INDEX module_code'
        ))
        db.session.commit()
        print('[4/4] courses.module_code unique constraint — dropped.')
    except Exception as e:
        db.session.rollback()
        print(f'[4/4] courses.module_code unique constraint — skipped ({e})')

    print('\nMigration complete.')
