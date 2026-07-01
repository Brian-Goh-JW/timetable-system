"""
Regenerate AY2526 (T1, T2, T3) from the command line.

Clears all non-backbone solver entries for AY2526, then runs the CP-SAT solver
for each trimester in sequence using the same logic as the web UI.

Run once (after bootstrap/18 has imported real professor/room data):
    python bootstrap/19_regenerate_ay2526.py
"""
import sys, os
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models.timetable_entry import TimetableEntry
from app.models.timetable_flag import TimetableFlag
from app.models.flag_response import FlagResponse
from app.models.class_session import ClassSession
from app.models.course import Course
from app.engine.solver import solve

ACADEMIC_YEAR   = 'AY2526'
START_DATES     = {1: '2025-09-01', 2: '2026-01-05', 3: '2026-05-04'}
TERM_BREAK_WEEKS = {7}


def _build_historical_preferred(academic_year, trimester_num):
    tri_key  = f'{academic_year}-T{trimester_num}'
    backbone = TimetableEntry.query.filter_by(trimester=tri_key, is_backbone=True).all()
    if backbone:
        preferred = {}
        for e in backbone:
            if e.class_session_id not in preferred:
                preferred[e.class_session_id] = e.timeslot_id
        return preferred
    try:
        y1 = int(academic_year[2:4])
        y2 = int(academic_year[4:6])
        prev_ay = f'AY{y1-1:02d}{y2-1:02d}'
    except ValueError:
        return {}
    prev_entries = TimetableEntry.query.filter_by(trimester=f'{prev_ay}-T{trimester_num}').all()
    preferred = {}
    for e in prev_entries:
        if e.class_session_id not in preferred:
            preferred[e.class_session_id] = e.timeslot_id
    return preferred


app = create_app()

with app.app_context():
    # --- Step 1: clear non-backbone entries for AY2526 ---
    tri_keys = [f'{ACADEMIC_YEAR}-T1', f'{ACADEMIC_YEAR}-T2', f'{ACADEMIC_YEAR}-T3']
    non_bb = TimetableEntry.query.filter(
        TimetableEntry.trimester.in_(tri_keys),
        TimetableEntry.is_backbone == False,
    ).all()
    if non_bb:
        ids = [e.id for e in non_bb]
        flag_ids = [f.id for f in TimetableFlag.query.filter(
            TimetableFlag.timetable_entry_id.in_(ids)).all()]
        if flag_ids:
            FlagResponse.query.filter(FlagResponse.flag_id.in_(flag_ids)).delete(synchronize_session=False)
            TimetableFlag.query.filter(TimetableFlag.id.in_(flag_ids)).delete(synchronize_session=False)
        TimetableEntry.query.filter(TimetableEntry.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
        print(f'Cleared {len(ids)} existing solver entries for {ACADEMIC_YEAR}.')
    else:
        print(f'No existing solver entries to clear for {ACADEMIC_YEAR}.')

    # --- Step 2: run solver for each trimester ---
    for tri_num in [1, 2, 3]:
        tri_key    = f'{ACADEMIC_YEAR}-T{tri_num}'
        start_date = date.fromisoformat(START_DATES[tri_num])
        historical_preferred = _build_historical_preferred(ACADEMIC_YEAR, tri_num)

        # When backbone exists for this AY, use it as hard pins (guarantees 1:1 similarity).
        # Incompatible or professor-blocked pins are dropped silently by the solver.
        backbone_entries = TimetableEntry.query.filter_by(trimester=tri_key, is_backbone=True).all()
        backbone_pins = {}
        if backbone_entries:
            for e in backbone_entries:
                if e.class_session_id not in backbone_pins:
                    backbone_pins[e.class_session_id] = e.timeslot_id

        print(f'\n{"="*60}')
        print(f'Solving {tri_key} (start={start_date}, backbone slots={len(historical_preferred)}, pins={len(backbone_pins)})...')
        print('='*60)

        try:
            success, message, stats = solve(
                tri_key, start_date, TERM_BREAK_WEEKS,
                trimester_num=tri_num,
                academic_year=ACADEMIC_YEAR,
                pinned_slots=backbone_pins or None,
                historical_preferred=historical_preferred,
            )
            if success:
                print(f'[OK] {tri_key}: {message}')
                for k, v in stats.items():
                    print(f'     {k}: {v}')
            else:
                print(f'[FAIL] {tri_key}: {message}')
        except Exception as exc:
            print(f'[ERROR] {tri_key}: {exc}')
            import traceback; traceback.print_exc()

    print('\nDone. Run the app and check the similarity report.')
