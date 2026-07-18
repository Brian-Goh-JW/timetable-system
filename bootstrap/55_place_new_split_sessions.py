"""
STEP 55 - Manually place the 21 new split sessions (bootstrap/53) into free
slots, after CP-SAT's full solve() (both pinned-existing and fresh-full
variants) came back infeasible/timed-out for the enlarged 246-session
scope - the same scaling wall this project hit before with the raw T1 set,
now re-triggered by adding these 21 sessions back in.

Since solve() only writes when it succeeds (confirmed safe - a failed
attempt never touches the existing schedule), those attempts left the
already-verified 225-session schedule completely untouched. This script
takes a narrower, safe path instead of another full CP-SAT pass: for each
of the 21 new sessions only, greedily search the TimeSlot catalog for a
slot where the (already very lightly booked) fixed room, the assigned
professor(s), and the linked student group are ALL free for every one of
the session's teaching weeks - checked against EVERY existing T1
TimetableEntry (not just the 8-programme scope) plus every other new
session already placed earlier in this same run, so two new siblings (e.g.
ENG1102's P2/P3/P4) can never collide with each other either.

This mirrors exactly the same hard constraints solver.py enforces
(professor/room/group no real-time overlap), just resolved directly instead
of through CP-SAT - appropriate here since the new sessions are a small,
tightly pre-determined set (room and professor already fixed by
bootstrap/53) rather than an open scheduling problem.

Run ONCE, after bootstrap/53:
    python bootstrap/55_place_new_split_sessions.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

from collections import defaultdict
from app import create_app, db
from app.models.class_session import ClassSession
from app.models.class_session_professor import ClassSessionProfessor
from app.models.timetable_entry import TimetableEntry
from app.models.timeslot import TimeSlot

app = create_app()

TRIMESTER = 'AY2526-T1'
ACADEMIC_YEAR = 'AY2526'

NEW_SESSION_IDS = [
    1498, 1499, 1500, 1501, 1502, 1503, 1504, 1505, 1506,
    1507, 1508, 1509, 1510, 1511, 1512, 1513, 1514, 1515,
    1516, 1517, 1518,
]


def parse_weeks(s):
    return {int(w) for w in (s or '').split(',') if w.strip().isdigit()}


def to_min(t):
    return t.hour * 60 + t.minute


def overlaps_time(ts_a, ts_b):
    if ts_a.day_of_week != ts_b.day_of_week:
        return False
    return to_min(ts_a.start_time) < to_min(ts_b.end_time) and to_min(ts_b.start_time) < to_min(ts_a.end_time)


with app.app_context():
    all_timeslots = TimeSlot.query.all()
    all_entries = TimetableEntry.query.filter_by(trimester=TRIMESTER, is_backbone=False).all()

    # busy[('room', room_id)] / [('prof', prof_id)] / [('group', group_id)] -> list of (timeslot, weeks)
    busy = defaultdict(list)
    for e in all_entries:
        wk = {e.week_number}
        if e.room_id:
            busy[('room', e.room_id)].append((e.timeslot, wk))
        cs = e.class_session
        for pid in cs.all_professor_ids:
            busy[('prof', pid)].append((e.timeslot, wk))
        if cs.student_group_id:
            busy[('group', cs.student_group_id)].append((e.timeslot, wk))

    def is_free(key, ts, weeks):
        for other_ts, other_weeks in busy[key]:
            if overlaps_time(ts, other_ts) and (weeks & other_weeks):
                return False
        return True

    def mark_busy(key, ts, weeks):
        busy[key].append((ts, weeks))

    placed = 0
    failed = []

    for sid in NEW_SESSION_IDS:
        s = db.session.get(ClassSession, sid)
        weeks = parse_weeks(s.teaching_weeks)
        candidates = [ts for ts in all_timeslots
                      if (to_min(ts.end_time) - to_min(ts.start_time)) // 60 == s.duration_hours]
        # deterministic order, spread across the week rather than always Monday morning
        candidates.sort(key=lambda ts: (ts.day_of_week, ts.start_time))

        prof_ids = s.all_professor_ids
        chosen = None
        for ts in candidates:
            if not is_free(('room', s.fixed_room_id), ts, weeks):
                continue
            if any(not is_free(('prof', pid), ts, weeks) for pid in prof_ids):
                continue
            if s.student_group_id and not is_free(('group', s.student_group_id), ts, weeks):
                continue
            chosen = ts
            break

        if not chosen:
            failed.append(s)
            print(f'  [FAIL] {s.course.module_code} {s.session_type} {s.group_label} (id={s.id}): '
                  f'no free slot found')
            continue

        for wk in sorted(weeks):
            db.session.add(TimetableEntry(
                class_session_id=s.id,
                timeslot_id=chosen.id,
                room_id=s.fixed_room_id,
                week_number=wk,
                trimester=TRIMESTER,
                academic_year=ACADEMIC_YEAR,
                is_published=False,
                is_manually_edited=True,
                is_backbone=False,
            ))

        mark_busy(('room', s.fixed_room_id), chosen, weeks)
        for pid in prof_ids:
            mark_busy(('prof', pid), chosen, weeks)
        if s.student_group_id:
            mark_busy(('group', s.student_group_id), chosen, weeks)

        placed += 1
        print(f'  [OK] {s.course.module_code} {s.session_type} {s.group_label} (id={s.id}) '
              f'-> {chosen.day_of_week} {chosen.start_time}-{chosen.end_time}')

    db.session.commit()
    print(f'\n{placed}/{len(NEW_SESSION_IDS)} placed, {len(failed)} failed.')
