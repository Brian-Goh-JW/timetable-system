"""
STEP 52 - Placeholder professors for 4 T2/T3 "no professor assigned" gaps.

Found 2026-07-18 while re-checking the Timetable page's "Generate" panel
after fixing checker.py's stale deferred-session count (see commit
"Fix stale professor-gap count..."): once that fix correctly excluded the
325 T1-deferred sessions, 4 genuinely new gaps became visible in T2/T3 -
these were never part of the original 7-gap sweep (which only looked at
T1). All 4 confirmed via direct query to have ZERO backbone TimetableEntry
rows (fully solver-generated), so they're fair game per Brian's standing
rule: generated data can be fabricated, real backbone data never touched.

  MET3700B workshop  T2  Fri 14:00-17:00        -> Placeholder Professor F
  EEE1200  lecture   T2  Tue 09:00-11:00         -> Placeholder Professor G
  EEE1200  lab       T2  Tue 13:00-16:00         -> Placeholder Professor G
                                                     (same module, no time
                                                     conflict - one instructor
                                                     running both)
  DSC3002A tutorial  T3  (own dedicated identity, same pattern as bootstrap
                          51's Professor E - DSC gets its own placeholder
                          per module rather than being mixed with other
                          programmes)                -> Placeholder Professor H

Reversible: delete these 3 User/Professor rows (cascades to their
ClassSessionProfessor assignments) to restore the original "no professor"
disclosed-gap state.

Run ONCE:
    python bootstrap/52_placeholder_profs_t2_t3_gaps.py
"""
import sys, os, secrets
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models.user import User
from app.models.professor import Professor
from app.models.class_session import ClassSession
from app.models.class_session_professor import ClassSessionProfessor

app = create_app()

PLACEHOLDERS = [
    ('F', 'Placeholder Professor F', 'placeholder.f@sit.edu.sg'),
    ('G', 'Placeholder Professor G', 'placeholder.g@sit.edu.sg'),
    ('H', 'Placeholder Professor H', 'placeholder.h@sit.edu.sg'),
]

ASSIGNMENTS = {
    1291: 'F',  # MET3700B workshop, T2
    1318: 'G',  # EEE1200 lecture, T2
    1319: 'G',  # EEE1200 lab, T2 (same prof as lecture, no time conflict)
    1445: 'H',  # DSC3002A tutorial, T3
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
        s = db.session.get(ClassSession, session_id)
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
        print(f'  [OK] session {session_id} ({c.module_code} {s.session_type}, T{s.trimester}) '
              f'-> Placeholder Professor {letter}')

    db.session.commit()
    print(f'\n{changed} assignment(s) made.')
