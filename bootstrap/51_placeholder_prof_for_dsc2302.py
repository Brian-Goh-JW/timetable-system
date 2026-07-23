"""
STEP 51 — Placeholder professor for DSC2302's own gap (generated, not backbone).

Companion to bootstrap/50. Brian, 2026-07-18: clarified the DSC boundary
is specifically the REAL backbone data (the real professor-submitted
timetable he provided as source data) - the solver's own GENERATED
entries (is_backbone=False) are fair game for fabrication like any other
programme's gaps. DSC2302 has zero backbone entries at all (confirmed via
direct query before bootstrap/50) - it's a plain, fully-generated session
with no professor assigned, same category as the other 5 already filled.

Creates one more clearly-marked placeholder (Placeholder Professor E,
staff_id PLACEHOLDER0005) and assigns it to both of DSC2302's sessions -
confirmed non-overlapping with each other (Monday lecture, Tuesday lab)
and with the 4 placeholders from bootstrap/50 (none of them are busy at
DSC2302's Monday 13:00 / Tuesday 13:00 slots, though a dedicated one is
used anyway for clarity - one placeholder identity per module rather than
mixing unrelated programmes under one fabricated "professor").

Run ONCE:
    python bootstrap/51_placeholder_prof_for_dsc2302.py
"""
import sys, os, secrets
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models.user import User
from app.models.professor import Professor
from app.models.class_session import ClassSession
from app.models.class_session_professor import ClassSessionProfessor

app = create_app()

NAME = 'Placeholder Professor E'
EMAIL = 'placeholder.e@sit.edu.sg'
SESSION_IDS = [1230, 1231]  # DSC2302 lecture (Mon 13:00), lab (Tue 13:00)

with app.app_context():
    existing = User.query.filter_by(email=EMAIL).first()
    if existing:
        prof = Professor.query.filter_by(user_id=existing.id).first()
    else:
        user = User(name=NAME, email=EMAIL, role='professor')
        user.set_password(secrets.token_urlsafe(24))
        db.session.add(user)
        db.session.flush()
        prof = Professor(user_id=user.id, staff_id='PLACEHOLDER0005',
                          department='DEMO - fabricated for coursework showcase')
        db.session.add(prof)
        db.session.flush()
    print(f'  [OK] {NAME} -> staff_id={prof.staff_id}')

    changed = 0
    for session_id in SESSION_IDS:
        s = db.session.get(ClassSession, session_id)
        if s is None:
            print(f'  [SKIP] session {session_id} not found')
            continue
        existing_assignment = ClassSessionProfessor.query.filter_by(
            session_id=session_id, professor_id=prof.id
        ).first()
        if existing_assignment:
            print(f'  [SKIP] session {session_id} already assigned')
            continue
        db.session.add(ClassSessionProfessor(
            session_id=session_id, professor_id=prof.id,
            is_primary=True, display_order=0,
        ))
        changed += 1
        c = s.course
        print(f'  [OK] session {session_id} ({c.module_code} {s.session_type}) -> {NAME}')

    db.session.commit()
    print(f'\n{changed} assignment(s) made.')
