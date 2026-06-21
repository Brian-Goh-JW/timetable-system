"""
Bootstrap 14 — Fix timeslot period labels and session durations.

Problems fixed:
  1. Thu/Wed/Fri 14:00-17:00 were labelled 'Lab PM2'.
     _slot_compatible() in solver.py treats any slot starting with 'Lab' as lab-only,
     which blocks DSC2311 and DSC3002A (both seminar type) from ever landing there.
     Historical preference is silently skipped -> solver places them wherever.
     Fix: rename those 3h afternoon slots to 'PM3'.

  2. DSC2311 (id=67) and DSC3002A (id=68) have duration_hours=2 but their real
     sessions are 3h (14:00-17:00). Even with the label fix, a 2h session can't
     match a 3h slot. Fix: set duration_hours=3.

  Mon 13:00-15:00 ('Lab PM2', 2h) is intentionally left unchanged — DSC2204 is
  a real lab and that label is correct.

Run:
    python bootstrap/14_fix_slot_labels_and_durations.py
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from datetime import time
from app import create_app, db
from app.models.timeslot import TimeSlot
from app.models.class_session import ClassSession

app = create_app()
with app.app_context():

    # -----------------------------------------------------------------------
    # Step 1: Rename 3h afternoon slots from 'Lab PM2' -> 'PM3'
    # -----------------------------------------------------------------------
    print('=== Step 1: Fix timeslot period labels ===')

    target_slots = [
        ('Thursday',  time(14, 0), time(17, 0)),
        ('Wednesday', time(14, 0), time(17, 0)),
        ('Friday',    time(14, 0), time(17, 0)),
    ]

    for day, start, end in target_slots:
        ts = TimeSlot.query.filter_by(
            day_of_week=day, start_time=start, end_time=end
        ).first()
        if ts:
            old_label = ts.period_label
            ts.period_label = 'PM3'
            print(f'  {day} {start}-{end}: "{old_label}" -> "PM3" (id={ts.id})')
        else:
            print(f'  {day} {start}-{end}: NOT FOUND (skipped)')

    db.session.commit()
    print()

    # -----------------------------------------------------------------------
    # Step 2: Fix session duration_hours for DSC2311 and DSC3002A
    # -----------------------------------------------------------------------
    print('=== Step 2: Fix session duration_hours ===')

    session_fixes = [
        (67, 3),  # DSC2311 seminar: real slot Thu 14:00-17:00 = 3h
        (68, 3),  # DSC3002A seminar: real slot Wed 14:00-17:00 = 3h
    ]

    for session_id, new_duration in session_fixes:
        s = db.session.get(ClassSession, session_id)
        if s:
            old_dur = s.duration_hours
            s.duration_hours = new_duration
            print(f'  Session {session_id} ({s.course.module_code} {s.session_type}): '
                  f'duration_hours {old_dur} -> {new_duration}')
        else:
            print(f'  Session {session_id}: NOT FOUND')

    db.session.commit()
    print()

    # -----------------------------------------------------------------------
    # Verify
    # -----------------------------------------------------------------------
    print('=== Verification ===')
    print('Timeslots at 14:00-17:00:')
    for ts in TimeSlot.query.filter_by(start_time=time(14, 0), end_time=time(17, 0)).all():
        print(f'  id={ts.id} {ts.day_of_week} label={ts.period_label}')

    print('Mon 13:00-15:00 (should still be Lab PM2):')
    ts_mon = TimeSlot.query.filter_by(
        day_of_week='Monday', start_time=time(13, 0), end_time=time(15, 0)
    ).first()
    if ts_mon:
        print(f'  id={ts_mon.id} label={ts_mon.period_label}')

    print()
    print('=== Done ===')
    print('Next steps:')
    print('  1. Re-generate AY2627 (all 3 trimesters)')
    print('  2. Run Cross-AY Similarity Report (AY2526 vs AY2627)')
    print('  3. DSC2311 should now land on Thu 14:00-17:00')
    print('  4. DSC3002A should now land on Wed 14:00-17:00')
    print('  5. Target: 85%+ similarity')
