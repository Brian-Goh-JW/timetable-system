"""
Migration: replace professor_id + professor_id_2 on class_sessions
           with the class_session_professors junction table.

Run once:
    python bootstrap/8_migrate_junction_professors.py
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import create_app, db

app = create_app()

with app.app_context():

    # 1. Create junction table
    try:
        db.session.execute(db.text('''
            CREATE TABLE IF NOT EXISTS class_session_professors (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                session_id    INT NOT NULL,
                professor_id  INT NOT NULL,
                is_primary    TINYINT(1) NOT NULL DEFAULT 0,
                display_order INT NOT NULL DEFAULT 0,
                CONSTRAINT fk_csp_session  FOREIGN KEY (session_id)   REFERENCES class_sessions(id) ON DELETE CASCADE,
                CONSTRAINT fk_csp_prof     FOREIGN KEY (professor_id) REFERENCES professors(id)     ON DELETE CASCADE
            )
        '''))
        db.session.commit()
        print('[1/4] class_session_professors table — created.')
    except Exception as e:
        db.session.rollback()
        print(f'[1/4] class_session_professors — skipped ({e})')

    # 2. Migrate existing professor_id and professor_id_2 data
    try:
        rows = db.session.execute(db.text(
            'SELECT id, professor_id, professor_id_2 FROM class_sessions '
            'WHERE professor_id IS NOT NULL OR professor_id_2 IS NOT NULL'
        )).fetchall()

        migrated = 0
        for row in rows:
            session_id, prof1, prof2 = row
            if prof1:
                db.session.execute(db.text(
                    'INSERT INTO class_session_professors '
                    '(session_id, professor_id, is_primary, display_order) '
                    'VALUES (:sid, :pid, 1, 0)'
                ), {'sid': session_id, 'pid': prof1})
                migrated += 1
            if prof2:
                db.session.execute(db.text(
                    'INSERT INTO class_session_professors '
                    '(session_id, professor_id, is_primary, display_order) '
                    'VALUES (:sid, :pid, 0, 1)'
                ), {'sid': session_id, 'pid': prof2})
                migrated += 1

        db.session.commit()
        print(f'[2/4] Migrated {migrated} professor assignments from existing columns.')
    except Exception as e:
        db.session.rollback()
        print(f'[2/4] Data migration — failed ({e})')

    # 3. Drop professor_id_2 column
    try:
        db.session.execute(db.text(
            'ALTER TABLE class_sessions DROP FOREIGN KEY fk_cs_prof2'
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

    try:
        db.session.execute(db.text(
            'ALTER TABLE class_sessions DROP COLUMN professor_id_2'
        ))
        db.session.commit()
        print('[3/4] class_sessions.professor_id_2 — dropped.')
    except Exception as e:
        db.session.rollback()
        print(f'[3/4] Drop professor_id_2 — skipped ({e})')

    # 4. Drop professor_id column
    try:
        db.session.execute(db.text(
            'ALTER TABLE class_sessions DROP FOREIGN KEY class_sessions_ibfk_2'
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

    try:
        db.session.execute(db.text(
            'ALTER TABLE class_sessions DROP COLUMN professor_id'
        ))
        db.session.commit()
        print('[4/4] class_sessions.professor_id — dropped.')
    except Exception as e:
        db.session.rollback()
        print(f'[4/4] Drop professor_id — skipped ({e})')

    print('\nMigration complete.')
