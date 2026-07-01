"""
1. Delete courses not in the real Excel timetable (Elective 1, Elective 2, USI2001).
2. Backfill AY2526BB backbone entries for the 9 real modules that were in the Excel
   timetable grids but missed by the original backbone import.
   Uses the current generated AY2526 timeslots as the backbone reference.

Run after bootstrap/18 and bootstrap/19:
    python bootstrap/20_backfill_backbone.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models.course import Course
from app.models.class_session import ClassSession
from app.models.timetable_entry import TimetableEntry
from app.models.timetable_flag import TimetableFlag
from app.models.flag_response import FlagResponse
from app.models.timeslot import TimeSlot

# Not in any real timetable Excel file — delete entirely
DELETE_MODULES = ['Elective 1', 'Elective 2', 'USI2001']

# In the Excel files but missed by the backbone import
BACKFILL_MODULES = [
    'DSC2204', 'DSC2302', 'DSC2311',
    'DSC3002A', 'DSC3002B', 'DSC3201',
    'INF2001', 'INF2005', 'UDE2222',
]

# Teaching weeks per trimester (term break week 7 excluded)
TRI_WEEKS = {
    1: [w for w in range(1, 14) if w != 7],
    2: [w for w in range(1, 14) if w != 7],
    3: [w for w in range(1, 13) if w != 7],
}

app = create_app()

with app.app_context():

    # -------------------------------------------------------------------------
    # Step 1: delete fake / non-timetable courses
    # -------------------------------------------------------------------------
    for code in DELETE_MODULES:
        course = Course.query.filter_by(module_code=code).first()
        if not course:
            print(f'[SKIP] {code}: not found in DB')
            continue

        sessions = ClassSession.query.filter_by(course_id=course.id).all()
        session_ids = [s.id for s in sessions]
        entry_count = 0

        if session_ids:
            entries = TimetableEntry.query.filter(
                TimetableEntry.class_session_id.in_(session_ids)).all()
            entry_ids = [e.id for e in entries]
            if entry_ids:
                flag_ids = [f.id for f in TimetableFlag.query.filter(
                    TimetableFlag.timetable_entry_id.in_(entry_ids)).all()]
                if flag_ids:
                    FlagResponse.query.filter(
                        FlagResponse.flag_id.in_(flag_ids)).delete(synchronize_session=False)
                    TimetableFlag.query.filter(
                        TimetableFlag.id.in_(flag_ids)).delete(synchronize_session=False)
                TimetableEntry.query.filter(
                    TimetableEntry.id.in_(entry_ids)).delete(synchronize_session=False)
                entry_count = len(entry_ids)

            for s in sessions:
                db.session.delete(s)

        db.session.delete(course)
        db.session.flush()
        print(f'[DELETED] {code}: {len(session_ids)} sessions, {entry_count} entries removed')

    db.session.commit()
    print()

    # -------------------------------------------------------------------------
    # Step 2: backfill backbone entries from generated AY2526 slots
    # -------------------------------------------------------------------------
    total_created = 0

    for code in BACKFILL_MODULES:
        course = Course.query.filter_by(module_code=code).first()
        if not course:
            print(f'[SKIP] {code}: not found in DB')
            continue

        tri_num = course.trimester               # 1, 2, or 3
        tri_key = f'AY2526-T{tri_num}'
        weeks   = TRI_WEEKS[tri_num]

        sessions = ClassSession.query.filter_by(course_id=course.id).all()

        for cs in sessions:
            # Check if backbone already exists
            existing = TimetableEntry.query.filter_by(
                class_session_id=cs.id,
                trimester=tri_key,
                is_backbone=True,
            ).count()
            if existing:
                print(f'[SKIP] {code} {cs.session_type} {tri_key}: already has backbone entries')
                continue

            # Find a generated entry to use as the timeslot reference
            gen_entry = TimetableEntry.query.filter_by(
                class_session_id=cs.id,
                trimester=tri_key,
                is_backbone=False,
            ).first()

            if not gen_entry:
                print(f'[SKIP] {code} {cs.session_type} {tri_key}: no generated entry to copy from')
                continue

            for week in weeks:
                db.session.add(TimetableEntry(
                    class_session_id=cs.id,
                    trimester=tri_key,
                    timeslot_id=gen_entry.timeslot_id,
                    room_id=gen_entry.room_id,
                    week_number=week,
                    is_backbone=True,
                    is_published=True,
                ))
            total_created += len(weeks)

            ts = db.session.get(TimeSlot, gen_entry.timeslot_id)
            slot_str = (f'{ts.day_of_week} {ts.start_time.strftime("%H:%M")}-{ts.end_time.strftime("%H:%M")}'
                        if ts else '?')
            print(f'[BB] {code} {cs.session_type} {tri_key}: {len(weeks)} entries @ {slot_str}')

        db.session.flush()

    db.session.commit()
    print(f'\nDone. {total_created} backbone entries created.')
    print('Next: python bootstrap/19_regenerate_ay2526.py')
