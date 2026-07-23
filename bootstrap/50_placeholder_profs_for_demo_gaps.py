"""
STEP 50 — Placeholder professors for 5 non-DSC "no professor assigned" gaps.

Brian, 2026-07-18: explicitly authorized fabricating placeholder data for
this demo/coursework submission ("this is not a real timetable... the goal
is to showcase that the system works... IT IS ALLOWED"), with one explicit
boundary: DSC's own gaps stay untouched (DSC2302's lecture/lab, still
missing a professor, is deliberately left as-is - not fabricated, no
backbone link either so it's a genuine pre-existing gap, just one Brian
asked to leave alone since it's "the DSC side").

Creates 4 clearly-marked placeholder Professor/User records - names and
staff IDs are obviously synthetic ("Placeholder Professor A" etc,
"PLACEHOLDER0001" etc) specifically so they can never be mistaken for a
real person's real commitment, unlike the DSC-fabrication incident this
project was burned by before (a wipe destroyed real backbone data after
an earlier fabrication). Assigned only to the 5 non-DSC sessions with no
professor and confirmed non-overlapping in time with each other:

  ASE2210  lecture   Mon 09:00           -> Placeholder Professor A
  MET4505  lectorial Mon 09:00           -> Placeholder Professor B
  MET4505  quiz      Tue 09:00           -> Placeholder Professor B (same
                                             professor, no time conflict -
                                             realistic: one instructor
                                             running both for their course)
  UDC1001  workshop  (async, no time)    -> Placeholder Professor C
  USI2001  workshop  Tue 13:00           -> Placeholder Professor D

Reversible: delete these 4 User/Professor rows (cascades to their
ClassSessionProfessor assignments) to restore the original "no professor"
disclosed-gap state.

Run ONCE:
    python bootstrap/50_placeholder_profs_for_demo_gaps.py
"""
import sys, os, secrets
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models.user import User
from app.models.professor import Professor
from app.models.class_session import ClassSession
from app.models.class_session_professor import ClassSessionProfessor
from app.models.course import Course

app = create_app()

PLACEHOLDERS = [
    ('A', 'Placeholder Professor A', 'placeholder.a@sit.edu.sg'),
    ('B', 'Placeholder Professor B', 'placeholder.b@sit.edu.sg'),
    ('C', 'Placeholder Professor C', 'placeholder.c@sit.edu.sg'),
    ('D', 'Placeholder Professor D', 'placeholder.d@sit.edu.sg'),
]

# session_id -> placeholder letter (session IDs from the live DB, confirmed
# via direct query before writing this script - see docstring above)
ASSIGNMENTS = {
    1497: 'A',  # ASE2210 lecture
    1083: 'B',  # MET4505 lectorial
    1084: 'B',  # MET4505 quiz
    1064: 'C',  # UDC1001 workshop (async)
    1074: 'D',  # USI2001 workshop
}

with app.app_context():
    letter_to_prof = {}
    for letter, name, email in PLACEHOLDERS:
        existing = User.query.filter_by(email=email).first()
        if existing:
            prof = Professor.query.filter_by(user_id=existing.id).first()
        else:
            user = User(name=name, email=email, role='professor')
            user.set_password(secrets.token_urlsafe(24))
            db.session.add(user)
            db.session.flush()
            prof = Professor(user_id=user.id, staff_id=f'PLACEHOLDER{ord(letter)-64:04d}',
                              department='DEMO - fabricated for coursework showcase')
            db.session.add(prof)
            db.session.flush()
        letter_to_prof[letter] = prof
        print(f'  [OK] {name} -> staff_id={prof.staff_id}')

    changed = 0
    for session_id, letter in ASSIGNMENTS.items():
        s = ClassSession.query.get(session_id)
        if s is None:
            print(f'  [SKIP] session {session_id} not found')
            continue
        existing_assignment = ClassSessionProfessor.query.filter_by(
            session_id=session_id, professor_id=letter_to_prof[letter].id
        ).first()
        if existing_assignment:
            print(f'  [SKIP] session {session_id} already assigned')
            continue
        db.session.add(ClassSessionProfessor(
            session_id=session_id, professor_id=letter_to_prof[letter].id,
            is_primary=True, display_order=0,
        ))
        changed += 1
        c = s.course
        print(f'  [OK] session {session_id} ({c.module_code} {s.session_type}) '
              f'-> Placeholder Professor {letter}')

    db.session.commit()
    print(f'\n{changed} assignment(s) made.')
