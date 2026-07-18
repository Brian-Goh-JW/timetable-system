"""
STEP 56 - ENG1102/ENG1103/ENG1104's lab split (bootstrap/53) hit a real
scheduling wall bootstrap/55 couldn't clear: all three share one very busy
cohort (student_group 46, the ISE year covered by these modules), which
between them already needed 12 three-hour lab slots out of only 18 that
exist in the whole week's TimeSlot catalog - on top of their own
lectorial/quiz/tutorial sessions in the same cohort's calendar. bootstrap/55
placed ENG1102's P2/P3 fine but ran out of legal slots for ENG1102's P4 and
all of ENG1103's/ENG1104's new sections (7 of 21 total).

Splitting into 4 rooms of ~25 each was mechanically "correct" (30-seat real
room, ceil(100/30)=4) but not the only fix available - the room itself was
already a fabricated choice for anything beyond the original P1 section
(Brian, 2026-07-18: explicitly authorized fabricating split data as needed).
A simpler, calendar-friendly alternative: put the WHOLE class back in ONE
session, just in a bigger real room. E2-08-01 (capacity 100, room_type=lab)
is a real room, completely unbooked in the current T1 schedule, and fits
all three modules' full 100-student intake with zero split needed - no new
calendar slots required for an already-packed cohort at all.

For each of ENG1102, ENG1103, ENG1104:
  - delete the P2/P3/P4 ClassSession rows bootstrap/53 created for it (and
    any ClassSessionProfessor / TimetableEntry rows that came with them -
    only 2 of these 9 sessions got as far as a TimetableEntry via
    bootstrap/55, the rest never placed)
  - re-point the original P1 session's fixed_room_id to E2-08-01
  - re-point its own existing TimetableEntry rows' room_id to match (same
    day/time as before - only the room changes)
  - recompute the group_label back to "All" (no more split siblings)

Run ONCE, after bootstrap/55:
    python bootstrap/56_widen_room_for_stuck_eng_labs.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

from app import create_app, db
from app.models.class_session import ClassSession
from app.models.class_session_professor import ClassSessionProfessor
from app.models.timetable_entry import TimetableEntry
from app.models.room import Room
from app.models.course import Course
from app.routes.admin import _recompute_group_labels

app = create_app()

MODULES = ['ENG1102', 'ENG1103', 'ENG1104']

with app.app_context():
    new_room = Room.query.filter_by(room_code='E2-08-01').first()
    assert new_room and new_room.capacity == 100 and new_room.room_type == 'lab'

    for code in MODULES:
        # Each of these 3 modules has TWO Course rows (EEE and ISE - see the
        # EEE/ISE shared-module family). Only ISE's had a fixed_room_id and
        # the "All"-turned-split session bootstrap/53 touched - EEE's own
        # P1/P2 pair (sequential across the term, no fixed room, deferred
        # from T1) must be left alone.
        sessions = (ClassSession.query.join(Course)
                    .filter(Course.module_code == code, ClassSession.session_type == 'lab',
                            Course.programme.has(code='ISE'))
                    .all())
        p1 = min(sessions, key=lambda s: s.id)  # original, lowest id = P1
        others = [s for s in sessions if s.id != p1.id]

        removed = 0
        for s in others:
            TimetableEntry.query.filter_by(class_session_id=s.id).delete()
            ClassSessionProfessor.query.filter_by(session_id=s.id).delete()
            db.session.delete(s)
            removed += 1

        old_room = p1.fixed_room.room_code if p1.fixed_room else None
        p1.fixed_room_id = new_room.id
        for e in TimetableEntry.query.filter_by(class_session_id=p1.id).all():
            e.room_id = new_room.id

        db.session.flush()
        _recompute_group_labels(p1.course_id, p1.session_type)

        print(f'  {code}: removed {removed} split session(s), P1 (id={p1.id}) room '
              f'{old_room} -> {new_room.room_code} (cap {new_room.capacity})')

    db.session.commit()
    print('\nDone.')
