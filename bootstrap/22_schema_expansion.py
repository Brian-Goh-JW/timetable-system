"""
STEP 22 — Schema expansion for ENG cluster support.

Adds to class_sessions:
  - session_type enum expanded: + lectorial, workshop, quiz
  - teaching_weeks VARCHAR(100): comma-separated active week numbers
  - is_async BOOLEAN: True = online-asynchronous (no timeslot assigned)
  - group_label VARCHAR(20): Template 2 group field (All / T1 / L1 / P1 ...)
  - preferred_timeslot_id FK: soft-preferred timeslot parsed from Remarks

Run ONCE after step 21:
    python bootstrap/22_schema_expansion.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db

app = create_app()

_STEPS = [
    # 1. Expand session_type enum
    (
        "class_sessions.session_type enum expanded",
        """ALTER TABLE class_sessions
           MODIFY COLUMN session_type
           ENUM('lecture','lab','seminar','tutorial','lectorial','workshop','quiz')
           NOT NULL""",
    ),
    # 2. teaching_weeks
    (
        "class_sessions.teaching_weeks added",
        "ALTER TABLE class_sessions ADD COLUMN teaching_weeks VARCHAR(100) NULL",
    ),
    # 3. is_async
    (
        "class_sessions.is_async added",
        "ALTER TABLE class_sessions ADD COLUMN is_async TINYINT(1) NOT NULL DEFAULT 0",
    ),
    # 4. group_label
    (
        "class_sessions.group_label added",
        "ALTER TABLE class_sessions ADD COLUMN group_label VARCHAR(20) NULL",
    ),
    # 5. preferred_timeslot_id
    (
        "class_sessions.preferred_timeslot_id added",
        """ALTER TABLE class_sessions
           ADD COLUMN preferred_timeslot_id INT NULL,
           ADD CONSTRAINT fk_cs_preferred_slot
           FOREIGN KEY (preferred_timeslot_id) REFERENCES timeslots(id)
           ON DELETE SET NULL""",
    ),
]

with app.app_context():
    for label, sql in _STEPS:
        try:
            db.session.execute(db.text(sql))
            db.session.commit()
            print(f'  [OK] {label}')
        except Exception as e:
            db.session.rollback()
            err = str(e)
            if '1060' in err or 'Duplicate column' in err or '1061' in err or '1091' in err:
                print(f'  [SKIP] {label} — already exists')
            elif '1292' in err or 'Data truncated' in err:
                print(f'  [SKIP] {label} — enum already includes new values')
            else:
                print(f'  [ERROR] {label}: {e}')
                raise

    print('\nMigration complete.')
