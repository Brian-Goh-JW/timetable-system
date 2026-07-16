"""
STEP 43 — Data fix: sync ClassSession.teaching_weeks from each session's own
real backbone TimetableEntry.week_number values, for every DSC-linked
session that has backbone data.

The solver only ever generates a TimetableEntry within a session's own
teaching_weeks (None means "every non-break week"). Found 2026-07-16: 23
backbone-linked sessions had a teaching_weeks value that didn't match what
the real backbone data actually recorded for that exact session - some
were cut short (e.g. a tutorial genuinely running the full 13-week term
had teaching_weeks limited to just 8 of them, so the solver never even
tried to schedule the rest), others were empty/None (treated by the solver
as "every week", wrongly making a one-off real Quiz repeat 13 times).
Both directions caused the generated schedule to under- or over-represent
the real backbone timetable, which is what Template 2's "Tri Week" column
was exporting incompletely.

This is the sync step the project's own history notes SHOULD already
happen after loading backbone data ("sync ClassSession.teaching_weeks from
the actual TimetableEntry.week_number values for affected sessions") but
evidently didn't for these 23 - possibly because they were touched by a
later, unrelated import pass after backbone was originally loaded.

Only touches sessions that have at least one is_backbone=True entry -
never changes teaching_weeks for a session with no real backbone data.

Run ONCE, then regenerate all 3 trimesters (Timetable page > Generate):
    python bootstrap/43_sync_backbone_teaching_weeks.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

from app import create_app, db
from app.models.class_session import ClassSession

app = create_app()

with app.app_context():
    changed = 0
    for s in ClassSession.query.all():
        bb_weeks = sorted(set(e.week_number for e in s.timetable_entries if e.is_backbone))
        if not bb_weeks:
            continue
        current = sorted(int(w) for w in s.teaching_weeks.split(',')) if s.teaching_weeks else []
        if current == bb_weeks:
            continue
        new_value = ','.join(str(w) for w in bb_weeks)
        print(f'  {s.course.module_code:12} {s.session_type:10} id={s.id:5} '
              f'"{s.teaching_weeks}" -> "{new_value}"')
        s.teaching_weeks = new_value
        changed += 1

    db.session.commit()
    print(f'\nDone. {changed} session(s) synced to their own real backbone weeks.')
