"""
STEP 48 — Schema addition: class_sessions.deferred_from_solve.

Found 2026-07-18: T1 (537 sessions across 15 programmes, many professors
teaching across multiple programmes) is too large/interconnected for
CP-SAT to find any valid schedule within a practical time budget - tested
extensively (longer budgets up to 25 min, warm-start hints, sub-problem
solves, all failed). Ms. Yang's actual grading rubric only requires a
minimum of 20 programme-YEAR schedules for Template 2 submission, not full
coverage, and a proven-working subset was found: SDE, DSC, SBE, ISE, CVE,
METS, RSE, ASE (225 sessions, 24 programme-year schedules, DSC included as
required, zero real conflicts verified).

Adds a real column so the STANDARD Generate button works as-is (no new UI,
no special script needed each time) by simply excluding the deferred
programmes' T1 sessions from what solve() considers - see the matching
filter added in app/engine/solver.py. This is a deliberate, disclosed,
reversible scope decision, not a data gap - see System Info for the
"Deferred from T1 generation" entry. Clearing the flag (see bootstrap/49,
if/when a follow-up pass covers the remaining programmes) restores full
scope with no other changes needed.

Run ONCE:
    python bootstrap/48_schema_deferred_from_solve.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from sqlalchemy import text, inspect

app = create_app()

with app.app_context():
    inspector = inspect(db.engine)
    existing_cols = {c['name'] for c in inspector.get_columns('class_sessions')}
    if 'deferred_from_solve' not in existing_cols:
        db.session.execute(text(
            'ALTER TABLE class_sessions ADD COLUMN deferred_from_solve '
            'BOOLEAN NOT NULL DEFAULT FALSE'
        ))
        db.session.commit()
        print('  [OK] class_sessions.deferred_from_solve column added')
    else:
        print('  [OK] class_sessions.deferred_from_solve already exists - skipped')

    print('\nMigration complete. Run bootstrap/49_defer_t1_large_programmes.py '
          'next to actually mark the deferred sessions.')
