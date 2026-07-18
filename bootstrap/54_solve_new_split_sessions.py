"""
STEP 54 - DEAD END, kept for the record. First attempt at placing the 21
new split sessions (bootstrap/53) into the T1 timetable via CP-SAT.

Tried two variants, both came back infeasible/timed out at the 400s budget:
  1. solve()'s "Preserve Existing / Option A" mechanism - every already-
     scheduled session's exact slot passed in as a hard pin via
     pinned_slots, leaving CP-SAT to place only the 21 new sessions.
  2. A full fresh re-solve of the whole in-scope set with no pinning at
     all (246 sessions total, the same call the Generate page itself
     makes) - the same scaling wall this project hit before with the raw
     T1 set (see the "T1 CP-SAT scaling wall" writeup elsewhere in this
     project's history) re-triggered by these 21 extra sessions.

Confirmed SAFE either way: solve() only deletes/rewrites TimetableEntry
rows AFTER a successful (OPTIMAL/FEASIBLE) solve (see solver.py's step 7),
so both failed attempts left the existing 225-session schedule completely
untouched. Superseded by bootstrap/55, which places the 21 new sessions
directly via a targeted greedy search instead of another CP-SAT pass.

Not meant to be re-run - kept only so the failed approach is on record.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

from datetime import date
from app import create_app
from app.models.timetable_entry import TimetableEntry
from app.engine.solver import solve
from app.routes.admin import (
    SIT_ACADEMIC_CALENDAR, _build_historical_preferred,
    _auto_create_flags, _save_solve_run,
)

app = create_app()

TRIMESTER = 'AY2526-T1'
ACADEMIC_YEAR = 'AY2526'
TRIMESTER_NUM = 1

with app.app_context():
    start_date = date.fromisoformat(SIT_ACADEMIC_CALENDAR[ACADEMIC_YEAR][TRIMESTER_NUM])
    term_break_weeks = {7}

    # First attempt (pinning all 225 existing slots as hard constraints) came
    # back infeasible - the shared-StudentGroup no-overlap rule means each
    # new split session must dodge EVERY one of its cohort's other already-
    # pinned classes for the week, which apparently leaves no legal slot for
    # at least one of the 21. Falling back to a full fresh re-solve of the
    # whole in-scope set (the same call the Generate page itself makes,
    # just with 21 more sessions in the pool) - the solver gets full
    # freedom to place everything together, same as the original 225-
    # session solve that already worked. Existing entries still feed in as
    # soft AddHint warm-start data (see solver.py) even without pinned_slots.
    historical_preferred = _build_historical_preferred(ACADEMIC_YEAR, TRIMESTER_NUM)

    success, message, stats = solve(
        TRIMESTER, start_date, term_break_weeks,
        trimester_num=TRIMESTER_NUM,
        academic_year=ACADEMIC_YEAR,
        historical_preferred=historical_preferred,
    )

    print(f'\nsuccess={success}')
    print(f'message={message}')
    if success:
        _auto_create_flags(TRIMESTER, stats.get('preferred_violations', []))
        _save_solve_run(TRIMESTER, stats)
        print(f'stats keys: {list(stats.keys())}')
        for k in ('total_sessions', 'scheduled_sessions', 'skipped_sessions', 'status'):
            if k in stats:
                print(f'  {k}: {stats[k]}')
