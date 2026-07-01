"""
Fix backbone entries for the 9 real modules so they match the actual Excel timeslots.

Steps:
  1. Add missing TimeSlot records to the DB (Tue 13-15, Wed 11-13, Thu 11-13).
  2. Delete the placeholder backbone entries created by bootstrap/20 for these modules.
  3. Create new backbone entries using the exact Excel timeslots.
  4. Leave DSC3002B as-is (IWSP full-day schedule has no standard timeslot).

Run after bootstrap/20:
    python bootstrap/21_fix_backbone_exact_slots.py
"""
import sys, os
from datetime import time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models.timeslot import TimeSlot
from app.models.course import Course
from app.models.class_session import ClassSession
from app.models.timetable_entry import TimetableEntry
from app.models.timetable_flag import TimetableFlag
from app.models.flag_response import FlagResponse

# Excel-derived mapping: (module_code, db_session_type) -> (day, start_str, end_str)
# Derived from openpyxl merged-cell parse of the 3 timetable Excel files.
# Where Excel uses a different label (e.g. "tutorial" vs DB "lab"), we match
# to the non-lecture ClassSession in the DB.
EXCEL_SLOTS = {
    ('DSC2204', 'lab'):      ('Monday',    '13:00', '15:00'),
    ('DSC2302', 'lecture'):  ('Wednesday', '09:00', '11:00'),
    ('DSC2302', 'lab'):      ('Friday',    '14:00', '16:00'),
    ('DSC2311', 'seminar'):  ('Thursday',  '14:00', '17:00'),
    ('DSC3002A', 'seminar'): ('Wednesday', '14:00', '17:00'),
    # DSC3002B: IWSP full-day (Tue 08:30-17:30) — no standard timeslot, skip
    ('DSC3201', 'lecture'):  ('Tuesday',   '13:00', '15:00'),   # new slot
    ('DSC3201', 'tutorial'): ('Wednesday', '11:00', '13:00'),   # new slot
    ('INF2001', 'lecture'):  ('Monday',    '09:00', '11:00'),
    ('INF2001', 'lab'):      ('Tuesday',   '16:00', '18:00'),
    ('INF2005', 'lecture'):  ('Monday',    '14:00', '16:00'),
    ('INF2005', 'lab'):      ('Thursday',  '11:00', '13:00'),   # new slot
    ('UDE2222', 'tutorial'): ('Thursday',  '14:00', '17:00'),
}

# New timeslots to add if not already present
NEW_TIMESLOTS = [
    ('Tuesday',   time(13, 0), time(15, 0), 'Lab PM2'),
    ('Wednesday', time(11, 0), time(13, 0), 'P1B'),
    ('Thursday',  time(11, 0), time(13, 0), 'P1B'),
]

TRI_WEEKS = {
    'AY2526-T1': [w for w in range(1, 14) if w != 7],
    'AY2526-T2': [w for w in range(1, 14) if w != 7],
    'AY2526-T3': [w for w in range(1, 13) if w != 7],
}

app = create_app()

with app.app_context():

    # ------------------------------------------------------------------
    # Step 1: ensure new timeslots exist
    # ------------------------------------------------------------------
    ts_cache = {}  # (day, start_str, end_str) -> timeslot_id

    # Populate cache from existing timeslots
    for ts in TimeSlot.query.all():
        key = (ts.day_of_week, ts.start_time.strftime('%H:%M'), ts.end_time.strftime('%H:%M'))
        ts_cache[key] = ts.id

    for day, t_start, t_end, label in NEW_TIMESLOTS:
        key = (day, t_start.strftime('%H:%M'), t_end.strftime('%H:%M'))
        if key in ts_cache:
            print(f'[TS] {day} {key[1]}-{key[2]}: already exists (id={ts_cache[key]})')
            continue
        new_ts = TimeSlot(
            day_of_week=day,
            start_time=t_start,
            end_time=t_end,
            period_label=label,
        )
        db.session.add(new_ts)
        db.session.flush()
        ts_cache[key] = new_ts.id
        print(f'[NEW TS] {day} {key[1]}-{key[2]} "{label}" id={new_ts.id}')

    db.session.commit()

    # ------------------------------------------------------------------
    # Step 2: replace backbone entries for each mapped session
    # ------------------------------------------------------------------
    total_created = 0

    for (mod_code, stype), (day, start_str, end_str) in EXCEL_SLOTS.items():
        course = Course.query.filter_by(module_code=mod_code).first()
        if not course:
            print(f'[SKIP] {mod_code}: course not found')
            continue

        cs = ClassSession.query.filter_by(course_id=course.id, session_type=stype).first()
        if not cs:
            print(f'[SKIP] {mod_code} {stype}: no ClassSession in DB')
            continue

        # Find the correct trimester
        tri_key = f'AY2526-T{course.trimester}'
        weeks   = TRI_WEEKS.get(tri_key, [])

        # Look up timeslot
        ts_key = (day, start_str, end_str)
        ts_id  = ts_cache.get(ts_key)
        if not ts_id:
            print(f'[SKIP] {mod_code} {stype}: timeslot {ts_key} not in cache')
            continue

        # Delete existing backbone entries for this session (with flag cleanup)
        old = TimetableEntry.query.filter_by(
            class_session_id=cs.id, trimester=tri_key, is_backbone=True).all()
        if old:
            old_ids = [e.id for e in old]
            flag_ids = [f.id for f in TimetableFlag.query.filter(
                TimetableFlag.timetable_entry_id.in_(old_ids)).all()]
            if flag_ids:
                FlagResponse.query.filter(
                    FlagResponse.flag_id.in_(flag_ids)).delete(synchronize_session=False)
                TimetableFlag.query.filter(
                    TimetableFlag.id.in_(flag_ids)).delete(synchronize_session=False)
            TimetableEntry.query.filter(
                TimetableEntry.id.in_(old_ids)).delete(synchronize_session=False)
        db.session.flush()

        # Get room from any existing generated entry
        gen = TimetableEntry.query.filter_by(
            class_session_id=cs.id, trimester=tri_key, is_backbone=False).first()
        room_id = gen.room_id if gen else None

        # Create new backbone entries
        for week in weeks:
            db.session.add(TimetableEntry(
                class_session_id=cs.id,
                trimester=tri_key,
                timeslot_id=ts_id,
                room_id=room_id,
                week_number=week,
                is_backbone=True,
                is_published=True,
            ))
        total_created += len(weeks)
        print(f'[BB] {mod_code:10s} {stype:10s} {tri_key}: {len(weeks)} entries @ {day} {start_str}-{end_str} (ts_id={ts_id})')

        db.session.flush()

    db.session.commit()
    print(f'\nDone. {total_created} backbone entries updated to exact Excel slots.')
    print('Next: python bootstrap/19_regenerate_ay2526.py')
