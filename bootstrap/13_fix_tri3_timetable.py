"""
Bootstrap 13 — Fix AY2526-T3 to match real SIT timetable (Brian's Year 1 Tri 3 group).

Changes:
  1. Add missing timeslots: Mon/Wed/Thu 13:00-15:00 or 14:00-17:00
  2. Fix session types/delivery for DSC2101, DSC2204, DSC2311, DSC3002A
  3. Re-seed AY2526-T3 historical entries with correct weekly slots

Real timetable (Brian's group, AY2526 Tri 3):
  DSC2101 Lecture  : Thu 09:00-11:00  (online most weeks, f2f sometimes)
  DSC2101 Lab P1   : Fri 09:00-12:00  E2-07-01
  DSC2204 Lab      : Mon 13:00-15:00  E6-04-15
  DSC2311 Workshop : Thu 14:00-17:00  E6-03-16
  DSC3002A Workshop: Wed 14:00-17:00  Online (W1 group only)

Run:
    python bootstrap/13_fix_tri3_timetable.py
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from datetime import time
from app import create_app, db
from app.models.timeslot import TimeSlot
from app.models.class_session import ClassSession
from app.models.timetable_entry import TimetableEntry
from app.models.room import Room
from app.models.academic_calendar import AcademicCalendar

app = create_app()
with app.app_context():

    # -----------------------------------------------------------------------
    # Step 1: Add missing timeslots
    # -----------------------------------------------------------------------
    print('=== Step 1: Add missing timeslots ===')
    new_slots = [
        # Mon 13:00-15:00 (2h lab/workshop — DSC2204)
        dict(day_of_week='Monday',    period_label='Lab PM2', start_time=time(13,0), end_time=time(15,0)),
        # Thu 14:00-17:00 (3h workshop — DSC2311)
        dict(day_of_week='Thursday',  period_label='Lab PM2', start_time=time(14,0), end_time=time(17,0)),
        # Wed 14:00-17:00 (3h workshop — DSC3002A)
        dict(day_of_week='Wednesday', period_label='Lab PM2', start_time=time(14,0), end_time=time(17,0)),
        # Fri 14:00-17:00 (3h — for intern group Friday sessions)
        dict(day_of_week='Friday',    period_label='Lab PM2', start_time=time(14,0), end_time=time(17,0)),
    ]

    added_slots = {}
    for s in new_slots:
        existing = TimeSlot.query.filter_by(
            day_of_week=s['day_of_week'],
            start_time=s['start_time'],
            end_time=s['end_time']
        ).first()
        if existing:
            print(f'  Already exists: {s["day_of_week"]} {s["start_time"]}-{s["end_time"]} (id={existing.id})')
            added_slots[f'{s["day_of_week"]}_{s["start_time"]}'] = existing
        else:
            ts = TimeSlot(**s)
            db.session.add(ts)
            db.session.flush()
            print(f'  Added: {s["day_of_week"]} {s["start_time"]}-{s["end_time"]} -> id={ts.id}')
            added_slots[f'{s["day_of_week"]}_{s["start_time"]}'] = ts

    db.session.commit()

    # -----------------------------------------------------------------------
    # Step 2: Fix session types and delivery modes
    # -----------------------------------------------------------------------
    print()
    print('=== Step 2: Fix session types ===')

    fixes = [
        # DSC2101 id=65: seminar -> lecture, f2f -> online
        # (Lecture is sometimes online, sometimes f2f — set as online as default since that's more common in Tri3)
        (65, 'lecture', 'online'),
        # DSC2204 id=66: lecture -> lab, stays f2f
        (66, 'lab', 'f2f'),
        # DSC2311 id=67: seminar stays, but delivery online -> f2f (E6-03-16 room)
        (67, 'seminar', 'f2f'),
        # DSC3002A id=68: tutorial -> seminar (workshop type), online
        (68, 'seminar', 'online'),
    ]

    for session_id, new_type, new_delivery in fixes:
        s = ClassSession.query.get(session_id)
        if s:
            old_type = s.session_type
            old_delivery = s.delivery_mode
            s.session_type = new_type
            s.delivery_mode = new_delivery
            print(f'  Session {session_id} ({s.course.module_code}): {old_type}/{old_delivery} -> {new_type}/{new_delivery}')
        else:
            print(f'  Session {session_id}: NOT FOUND')

    db.session.commit()

    # -----------------------------------------------------------------------
    # Step 3: Re-seed AY2526-T3 historical entries
    # -----------------------------------------------------------------------
    print()
    print('=== Step 3: Re-seed AY2526-T3 historical entries ===')

    TRI_KEY = 'AY2526-T3'
    AY      = 'AY2526'

    # Clear existing T3 entries
    existing = TimetableEntry.query.filter_by(trimester=TRI_KEY).count()
    if existing:
        TimetableEntry.query.filter_by(trimester=TRI_KEY).delete()
        AcademicCalendar.query.filter_by(trimester=TRI_KEY).delete()
        db.session.commit()
        print(f'  Cleared {existing} existing {TRI_KEY} entries')

    # Helper: get timeslot by day + start + end
    def get_ts(day, st, et):
        return TimeSlot.query.filter_by(
            day_of_week=day, start_time=st, end_time=et
        ).first()

    # Helper: get room
    def get_room(code):
        return Room.query.filter(Room.room_code.ilike(f'%{code}%')).first()

    # Timeslots we need
    ts_thu_0911 = get_ts('Thursday',  time(9,0),  time(11,0))  # id=22
    ts_fri_0912 = get_ts('Friday',    time(9,0),  time(12,0))  # id=33
    ts_mon_1315 = get_ts('Monday',    time(13,0), time(15,0))  # newly added
    ts_thu_1417 = get_ts('Thursday',  time(14,0), time(17,0))  # newly added
    ts_wed_1417 = get_ts('Wednesday', time(14,0), time(17,0))  # newly added

    # Rooms
    room_e6_03_16 = get_room('E6-03-16')
    room_e2_07_01 = get_room('E2-07-01')
    room_e6_04_15 = get_room('E6-04-15')

    print(f'  Thu 09-11: {ts_thu_0911}')
    print(f'  Fri 09-12: {ts_fri_0912}')
    print(f'  Mon 13-15: {ts_mon_1315}')
    print(f'  Thu 14-17: {ts_thu_1417}')
    print(f'  Wed 14-17: {ts_wed_1417}')
    print(f'  E6-03-16: {room_e6_03_16}')
    print(f'  E2-07-01: {room_e2_07_01}')
    print(f'  E6-04-15: {room_e6_04_15}')

    # Sessions (after fix)
    # id=64 DSC2101 lab   (Fri 09:00-12:00, E2-07-01)
    # id=65 DSC2101 lecture (Thu 09:00-11:00, online/E6-03-16)
    # id=66 DSC2204 lab   (Mon 13:00-15:00, E6-04-15)
    # id=67 DSC2311 seminar (Thu 14:00-17:00, E6-03-16)
    # id=68 DSC3002A seminar (Wed 14:00-17:00, online)

    # Weeks 1-6 of Tri 3 (4 May - 14 Jun 2026), Week 7 = term break
    # Week 1 starts Mon 4 May 2026
    # Week 5 (1 Jun) = Learning Journey — DSC2204 cancelled
    # All other sessions run every week 1-6

    entries_to_add = []

    for week_num in range(1, 7):  # weeks 1-6
        # DSC2101 Lecture — Thu 09:00-11:00, online (every week)
        if ts_thu_0911:
            entries_to_add.append(TimetableEntry(
                class_session_id  = 65,
                timeslot_id       = ts_thu_0911.id,
                room_id           = room_e6_03_16.id if room_e6_03_16 and week_num in (1, 6) else None,
                week_number       = week_num,
                trimester         = TRI_KEY,
                academic_year     = AY,
                is_published      = False,
                is_manually_edited= True,
            ))

        # DSC2101 Lab P1 — Fri 09:00-12:00, E2-07-01 (weeks 1,3,4,5,6; skip week 2)
        if ts_fri_0912 and week_num != 2:
            entries_to_add.append(TimetableEntry(
                class_session_id  = 64,
                timeslot_id       = ts_fri_0912.id,
                room_id           = room_e2_07_01.id if room_e2_07_01 else None,
                week_number       = week_num,
                trimester         = TRI_KEY,
                academic_year     = AY,
                is_published      = False,
                is_manually_edited= True,
            ))

        # DSC2204 Lab — Mon 13:00-15:00, E6-04-15 (skip week 5 = Learning Journey)
        if ts_mon_1315 and week_num != 5:
            entries_to_add.append(TimetableEntry(
                class_session_id  = 66,
                timeslot_id       = ts_mon_1315.id,
                room_id           = room_e6_04_15.id if room_e6_04_15 else None,
                week_number       = week_num,
                trimester         = TRI_KEY,
                academic_year     = AY,
                is_published      = False,
                is_manually_edited= True,
            ))

        # DSC2311 Workshop — Thu 14:00-17:00, E6-03-16 (every week)
        if ts_thu_1417:
            entries_to_add.append(TimetableEntry(
                class_session_id  = 67,
                timeslot_id       = ts_thu_1417.id,
                room_id           = room_e6_03_16.id if room_e6_03_16 else None,
                week_number       = week_num,
                trimester         = TRI_KEY,
                academic_year     = AY,
                is_published      = False,
                is_manually_edited= True,
            ))

        # DSC3002A Workshop W1 — Wed 14:00-17:00, Online (week 2 only from data)
        if ts_wed_1417 and week_num == 2:
            entries_to_add.append(TimetableEntry(
                class_session_id  = 68,
                timeslot_id       = ts_wed_1417.id,
                room_id           = None,  # online
                week_number       = week_num,
                trimester         = TRI_KEY,
                academic_year     = AY,
                is_published      = False,
                is_manually_edited= True,
            ))

    for e in entries_to_add:
        db.session.add(e)
    db.session.commit()

    total = TimetableEntry.query.filter_by(trimester=TRI_KEY).count()
    print(f'\n  Created {total} entries for {TRI_KEY}')
    print()
    print('=== Done ===')
    print('Next steps:')
    print('  1. Check Admin -> Timetable -> AY25/26 Tri 3 tab to verify entries')
    print('  2. Re-generate AY2627 to pick up the updated T3 historical data')
    print('  3. Cross-AY comparison should now show T3 modules as Kept instead of New')
