"""
Bootstrap 17 — Seed professor availability from backbone entries.

For each AY2526 backbone entry:
  - Find the professor teaching that ClassSession
  - If they have a strict AvailabilityDeclaration for that timeslot,
    downgrade it to 'preferred' (they clearly can teach there — they did)
  - If preferred, remove it entirely (no conflict at all)

This ensures the solver is not hard-blocked from historical slots when
regenerating AY2526 or producing AY2627.

Usage:
    python bootstrap/17_seed_availability_from_backbone.py
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models.timetable_entry import TimetableEntry
from app.models.availability_declaration import AvailabilityDeclaration

app = create_app()
with app.app_context():
    backbone = TimetableEntry.query.filter_by(is_backbone=True).all()

    downgraded = 0
    removed    = 0
    checked    = set()  # (prof_id, timeslot_id) already processed

    for e in backbone:
        ts_id = e.timeslot_id
        cs    = e.class_session
        profs = list(cs.all_professors)
        if e.override_professor:
            profs = [e.override_professor]

        for prof in profs:
            key = (prof.id, ts_id)
            if key in checked:
                continue
            checked.add(key)

            decl = AvailabilityDeclaration.query.filter_by(
                professor_id=prof.id,
                timeslot_id=ts_id,
            ).first()

            if not decl:
                continue

            if decl.constraint_type == 'strict':
                decl.constraint_type = 'preferred'
                decl.reason = (decl.reason or '') + ' [auto-downgraded: professor taught this slot in AY2526 backbone]'
                downgraded += 1
                print(f'  Downgraded strict→preferred: {prof.user.name} slot {ts_id}')
            else:
                db.session.delete(decl)
                removed += 1
                print(f'  Removed preferred conflict: {prof.user.name} slot {ts_id}')

    db.session.commit()
    print(f'\nDone. Downgraded: {downgraded}  Removed: {removed}  Total backbone slots checked: {len(checked)}')
