"""
STEP 49 — Mark T1's 7 largest programmes as deferred_from_solve.

Companion to bootstrap/48 (adds the column). Sets deferred_from_solve=True
for every Trimester-1 ClassSession belonging to EDE, EEE, EPE, ESE, MDME,
MEC, or NAME - the 7 programmes NOT in the proven-working 225-session /
24-programme-year-schedule set (SDE, DSC, SBE, ISE, CVE, METS, RSE, ASE).
Scoped to trimester=1 only - these same programmes' T2/T3 sessions are
untouched (T2 and T3 already generate successfully at full scope, no
scope reduction needed there).

This is reversible: to restore full T1 scope later (e.g. once a follow-up
generation pass or a better solver strategy is available), just re-run
with CLEAR=True below, or set the flag back to False directly.

Run ONCE:
    python bootstrap/49_defer_t1_large_programmes.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models.class_session import ClassSession
from app.models.course import Course
from app.models.programme import Programme

app = create_app()

DEFER_PROGS = {'EDE', 'EEE', 'EPE', 'ESE', 'MDME', 'MEC', 'NAME'}
CLEAR = False  # set True and re-run to undo (clears the flag instead of setting it)

with app.app_context():
    sessions = (ClassSession.query
                .join(Course).join(Programme)
                .filter(ClassSession.trimester == 1, Programme.code.in_(DEFER_PROGS))
                .all())
    print(f'Found {len(sessions)} T1 sessions across {sorted(DEFER_PROGS)}.')

    changed = 0
    for s in sessions:
        target = False if CLEAR else True
        if s.deferred_from_solve != target:
            s.deferred_from_solve = target
            changed += 1

    db.session.commit()
    action = 'cleared' if CLEAR else 'set'
    print(f'{action} deferred_from_solve for {changed} session(s).')

    kept = ClassSession.query.filter_by(trimester=1, deferred_from_solve=False).count()
    deferred = ClassSession.query.filter_by(trimester=1, deferred_from_solve=True).count()
    print(f'T1 totals: {kept} ready for generation, {deferred} deferred.')
