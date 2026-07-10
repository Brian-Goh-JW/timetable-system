"""
STEP 34 — Add class_sessions.fixed_room_id (locks a session to one specific
room, the way fixed_timeslot_id already locks a session to one specific
time). Needed to use the exact venue codes some of the cleaned data files
carry per session (CVE, METS, ISE, part of EEE) - previously ignored
entirely, the solver picked every room itself.

Run ONCE:
    python bootstrap/34_schema_fixed_room.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db

app = create_app()

with app.app_context():
    try:
        db.session.execute(db.text(
            """ALTER TABLE class_sessions
               ADD COLUMN fixed_room_id INT NULL,
               ADD CONSTRAINT fk_cs_fixed_room
               FOREIGN KEY (fixed_room_id) REFERENCES rooms(id)
               ON DELETE SET NULL"""
        ))
        db.session.commit()
        print('  [OK] class_sessions.fixed_room_id added')
    except Exception as e:
        db.session.rollback()
        err = str(e)
        if '1060' in err or 'Duplicate column' in err:
            print('  [SKIP] class_sessions.fixed_room_id - already exists')
        else:
            print(f'  [ERROR] {e}')
            raise

    print('\nMigration complete.')
