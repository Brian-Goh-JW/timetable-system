"""
Fix backbone timeslot mismatches for AY2526.

The original import matched lab sessions to 2hr lecture timeslots (P1/P3/P4)
instead of the correct 3hr lab timeslots (Lab AM/PM/EV/PM3).  This meant the
solver's historical soft constraint was silently skipped (duration mismatch),
so the solver placed those sessions freely instead of honouring the backbone.

This script updates the 5 affected backbone entries to the correct timeslot_id.

Run once:
    python bootstrap/fix_backbone_timeslots.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models.timetable_entry import TimetableEntry
from app.models.timeslot import TimeSlot
from app.models.class_session import ClassSession
from app.models.course import Course

app = create_app()

# Each tuple: (trimester, module_code, session_type, current_timeslot_id, correct_timeslot_id)
# Correct IDs confirmed from timeslot listing:
#   Fri Lab AM  id=33  09:00-12:00  (replaces Fri P1 id=29 for labs)
#   Thu PM3     id=37  14:00-17:00  (replaces Thu P3 id=24 for labs starting 14:00)
#   Fri Lab EV  id=35  16:00-19:00  (replaces Fri P4 id=32 for labs starting 16:00)
#   Fri P1      id=29  09:00-11:00  (replaces Fri Lab AM id=33 for DSC3001 lecture)
FIXES = [
    ('AY2526-T1', 'INF1002',  'lab',     29, 33),   # Fri P1 -> Fri Lab AM
    ('AY2526-T2', 'DSC2602',  'lab',     24, 37),   # Thu P3 -> Thu PM3
    ('AY2526-T2', 'INF1005',  'lab',     32, 35),   # Fri P4 -> Fri Lab EV
    ('AY2526-T2', 'INF2008',  'lab',     24, 37),   # Thu P3 -> Thu PM3
    ('AY2526-T2', 'DSC3001',  'lecture', 33, 29),   # Fri Lab AM -> Fri P1
]

with app.app_context():
    total_updated = 0

    for tri, mod, stype, old_id, new_id in FIXES:
        old_ts = TimeSlot.query.get(old_id)
        new_ts = TimeSlot.query.get(new_id)
        if not old_ts or not new_ts:
            print(f'[!] Timeslot id {old_id} or {new_id} not found — skipping {mod} {stype}')
            continue

        entries = (
            TimetableEntry.query
            .join(TimetableEntry.class_session)
            .join(ClassSession.course)
            .filter(
                TimetableEntry.trimester == tri,
                TimetableEntry.is_backbone == True,
                TimetableEntry.timeslot_id == old_id,
                Course.module_code == mod,
                ClassSession.session_type == stype,
            ).all()
        )

        if not entries:
            print(f'[!] No backbone entries found for {mod} {stype} in {tri} at timeslot {old_id}')
            continue

        for e in entries:
            e.timeslot_id = new_id

        db.session.commit()
        print(
            f'[OK] {mod} {stype} ({tri}): '
            f'{old_ts.day_of_week} {old_ts.period_label} {old_ts.start_time.strftime("%H:%M")}-{old_ts.end_time.strftime("%H:%M")} (id={old_id}) '
            f'-> {new_ts.day_of_week} {new_ts.period_label} {new_ts.start_time.strftime("%H:%M")}-{new_ts.end_time.strftime("%H:%M")} (id={new_id}) '
            f'({len(entries)} entries updated)'
        )
        total_updated += len(entries)

    print(f'\nDone. {total_updated} backbone entries updated.')
    print('Next: regenerate AY2526 and check the similarity report.')
