"""
STEP 40 — Data cleanup: remove ClassSessions whose teaching_weeks is
entirely outside this system's 13-week trimester calendar.

Follows on from STEP 29 (fix_quiz_teaching_weeks.py), which already
confirmed with Brian that week 13 is exam week - values of 14, 15, 17, 30
etc. in teaching_weeks are data entry mistakes, not a real convention.
That script fixed every record where OTHER valid weeks remained after
stripping the bad value, but deliberately left untouched any record whose
ONLY value was invalid (stripping it would leave nothing, and there was no
way to know what the correct week should have been) - printed for manual
follow-up, never resolved until now.

Confirmed with Brian (16 July 2026): these are genuinely not real - safe
to remove the session entirely rather than guess a replacement week. Only
touches sessions that were ALREADY invisible from every export (found via
solver.py's 2026-07-16 fix: a session whose only teaching week doesn't
exist in the calendar can never produce a real TimetableEntry no matter
which day/room is picked) - this just removes the now-pointless row and
its dangling professor-assignment link(s) instead of leaving a permanent
"Sessions Skipped" warning on every future generate.

Run ONCE:
    python bootstrap/40_remove_invalid_teaching_week_sessions.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

from app import create_app, db
from app.models.class_session import ClassSession

MAX_VALID_WEEK = 13

app = create_app()

with app.app_context():
    sessions = ClassSession.query.filter(ClassSession.teaching_weeks.isnot(None)).all()

    to_remove = []
    for s in sessions:
        weeks = [w.strip() for w in s.teaching_weeks.split(',') if w.strip()]
        try:
            nums = [int(w) for w in weeks]
        except ValueError:
            continue
        if nums and all(n < 1 or n > MAX_VALID_WEEK for n in nums):
            to_remove.append(s)

    print(f'Removing {len(to_remove)} session(s) with no valid teaching week:')
    for s in to_remove:
        print(f'  session={s.id:5} {s.course.module_code:12} {s.session_type:10} '
              f'trimester={s.trimester} weeks="{s.teaching_weeks}" '
              f'(had {len(s.professor_assignments)} professor link(s))')
        db.session.delete(s)  # cascades to ClassSessionProfessor via 'all, delete-orphan'

    db.session.commit()
    print(f'\nDone. {len(to_remove)} session(s) removed.')
