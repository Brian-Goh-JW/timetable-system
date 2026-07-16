"""
STEP 46 - Data fix: link 2 lecture sessions to the student group their own
sibling sessions already use.

Found during a Template 2 export spot-check (17 July 2026): ASE1011's
lecture (group label "L1") and ENG1005's lecture (group label "All") both
had student_group_id = None, leaving Class Size blank in the export - while
every other session in the same module (tutorials/labs/quizzes) correctly
pointed to a real StudentGroup. Not a missing cohort like the ASE-Y3 case
(see bootstrap/44) - the group already exists and is already used elsewhere
in the same module, just never linked to these two specific sessions.

    ASE1011 lecture (session id 59)  -> student_group_id 8  (ASE-Y1)
    ENG1005 lecture (session id 971) -> student_group_id 43 (METS-Y1)

Confirmed with Brian (17 July 2026) to link them rather than leave blank.

Run ONCE:
    python bootstrap/46_fix_orphan_lecture_groups.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

from app import create_app, db
from app.models.class_session import ClassSession
from app.models.student_group import StudentGroup

FIXES = [
    # (session_id, expected module, expected type, target_group_id)
    (59,  'ASE1011', 'lecture', 8),
    (971, 'ENG1005', 'lecture', 43),
]

app = create_app()

with app.app_context():
    for session_id, expected_mod, expected_type, group_id in FIXES:
        s = ClassSession.query.get(session_id)
        if s is None:
            print(f'  SKIP: session {session_id} not found.')
            continue
        if s.course.module_code != expected_mod or s.session_type != expected_type:
            print(f'  SKIP: session {session_id} is {s.course.module_code} '
                  f'{s.session_type}, expected {expected_mod} {expected_type} - data may have changed.')
            continue
        if s.student_group_id is not None:
            print(f'  SKIP: session {session_id} ({expected_mod} {expected_type}) '
                  f'already has student_group_id={s.student_group_id} - leaving as is.')
            continue
        group = StudentGroup.query.get(group_id)
        if group is None:
            print(f'  SKIP: target group {group_id} not found.')
            continue
        s.student_group_id = group_id
        print(f'  {expected_mod} {expected_type} (session {session_id}) -> '
              f'group {group_id} ({group.group_label})')

    db.session.commit()
    print('\nDone.')
