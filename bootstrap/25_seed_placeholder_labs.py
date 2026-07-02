"""
Bootstrap 25 — Seed placeholder department lab rooms.

These rooms represent real ENG/DSC department-specific laboratories that exist at SIT
but were not included in the original room data import.  They are clearly marked with
square brackets so they can be identified and replaced with real room data later.

Capacity is set to the maximum student-group intake across all sessions for that
department, rounded up to the next multiple of 10.  MEC gets extra capacity for the
140-student cohort classes.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app, db
from app.models.room import Room

PLACEHOLDER_LABS = [
    # (room_code,        building,   capacity, room_type)
    # ASE — max intake 75
    ('[ASE-Lab-1]',      '[ASE]',    100,      'lab'),

    # CVE — max intake 100, up to 2 concurrent fixed sessions
    ('[CVE-Lab-1]',      '[CVE]',    120,      'lab'),
    ('[CVE-Lab-2]',      '[CVE]',    120,      'lab'),

    # DSC — max intake 70
    ('[DSC-Lab-1]',      '[DSC]',    100,      'lab'),

    # EDE — max intake 70, 9 sessions (split across term break → ~5 effective slots needed)
    ('[EDE-Lab-1]',      '[EDE]',    100,      'lab'),
    ('[EDE-Lab-2]',      '[EDE]',    100,      'lab'),

    # ENG — max intake 100, 6 sessions (mixed groups)
    ('[ENG-Lab-1]',      '[ENG]',    120,      'lab'),
    ('[ENG-Lab-2]',      '[ENG]',    120,      'lab'),

    # EPE — max intake 95, 4 sessions
    ('[EPE-Lab-1]',      '[EPE]',    120,      'lab'),
    ('[EPE-Lab-2]',      '[EPE]',    120,      'lab'),

    # INF — max intake 80, 4 sessions
    ('[INF-Lab-1]',      '[INF]',    100,      'lab'),
    ('[INF-Lab-2]',      '[INF]',    100,      'lab'),

    # MEC — max intake 140 (MEC1151/MEC1161), 14+ sessions, up to 3 concurrent fixed
    ('[MEC-Lab-1]',      '[MEC]',    150,      'lab'),
    ('[MEC-Lab-2]',      '[MEC]',    150,      'lab'),
    ('[MEC-Lab-3]',      '[MEC]',    150,      'lab'),

    # MET — max intake 58, 9 sessions
    ('[MET-Lab-1]',      '[MET]',    100,      'lab'),
    ('[MET-Lab-2]',      '[MET]',    100,      'lab'),

    # MME — max intake 85, 1 session
    ('[MME-Lab-1]',      '[MME]',    100,      'lab'),
]


def run():
    app = create_app()
    with app.app_context():
        added = 0
        skipped = 0
        for room_code, building, capacity, room_type in PLACEHOLDER_LABS:
            existing = Room.query.filter_by(room_code=room_code).first()
            if existing:
                skipped += 1
                print(f'  SKIP  {room_code} (already exists)')
                continue
            room = Room(
                room_code=room_code,
                building=building,
                capacity=capacity,
                room_type=room_type,
                is_active=True,
            )
            db.session.add(room)
            added += 1
            print(f'  ADD   {room_code}  building={building}  cap={capacity}  type={room_type}')

        db.session.commit()
        print(f'\nDone — added {added} placeholder lab rooms, skipped {skipped} already present.')
        print('These rooms are placeholder data.  Replace with real room codes when available.')


if __name__ == '__main__':
    run()
