"""
Bootstrap 11 — Seed mandatory recurring events for DSC programme.

These are sample dates based on typical SIT AY2526 calendar.
Edit actual dates through Admin → Events once confirmed.

Run once:
    python bootstrap/11_seed_mandatory_events.py
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from datetime import date
from app import create_app, db
from app.models.event import Event
from app.models.programme import Programme

app = create_app()
with app.app_context():

    # Only seed if no events exist yet
    existing = Event.query.count()
    if existing > 0:
        print(f"Events table already has {existing} record(s). Skipping seed.")
        print("Delete existing events via Admin > Events if you want to re-seed.")
        sys.exit(0)

    # Get DSC programme (for programme-scoped events)
    dsc = Programme.query.filter_by(code='DSC').first()
    dsc_id = dsc.id if dsc else None

    events = [
        # ---------------------------------------------------------------
        # DSC Poster Day — end of Tri 1 and Tri 3, DSC-specific
        # Full day: no scheduled classes
        # ---------------------------------------------------------------
        dict(
            name         = 'DSC Poster Day',
            description  = 'DSC Year-end poster presentation. All DSC classes cancelled for the day.',
            event_date   = date(2025, 11, 14),   # Week 12 of Tri 1 (assumed Friday)
            is_full_day  = True,
            scope        = 'programme',
            programme_id = dsc_id,
            outcome      = 'cancel',
            trimester    = 1,
            academic_year= 'AY2526',
            is_recurring = False,
        ),
        dict(
            name         = 'DSC Poster Day',
            description  = 'DSC Year-end poster presentation. All DSC classes cancelled for the day.',
            event_date   = date(2026, 7, 10),    # Week 12 of Tri 3 (assumed Friday)
            is_full_day  = True,
            scope        = 'programme',
            programme_id = dsc_id,
            outcome      = 'cancel',
            trimester    = 3,
            academic_year= 'AY2526',
            is_recurring = False,
        ),

        # ---------------------------------------------------------------
        # Career Nexus — annual SIT career fair, school-wide, Tri 2
        # Full day: no scheduled classes
        # ---------------------------------------------------------------
        dict(
            name         = 'Career Nexus',
            description  = 'SIT annual career fair. School-wide event — all classes cancelled.',
            event_date   = date(2026, 2, 20),    # Week 6 of Tri 2 (assumed Friday)
            is_full_day  = True,
            scope        = 'school_wide',
            programme_id = None,
            outcome      = 'cancel',
            trimester    = 2,
            academic_year= 'AY2526',
            is_recurring = True,   # happens every AY
        ),

        # ---------------------------------------------------------------
        # Learning Journey — once per trimester, DSC-specific
        # Full day: classes for that day are cancelled
        # ---------------------------------------------------------------
        dict(
            name         = 'Learning Journey (Tri 1)',
            description  = 'DSC Learning Journey — industry visit. All DSC classes cancelled.',
            event_date   = date(2025, 9, 26),    # Week 5 of Tri 1 (assumed Friday)
            is_full_day  = True,
            scope        = 'programme',
            programme_id = dsc_id,
            outcome      = 'cancel',
            trimester    = 1,
            academic_year= 'AY2526',
            is_recurring = False,
        ),
        dict(
            name         = 'Learning Journey (Tri 2)',
            description  = 'DSC Learning Journey — industry visit. All DSC classes cancelled.',
            event_date   = date(2026, 2, 6),     # Week 5 of Tri 2 (assumed Friday)
            is_full_day  = True,
            scope        = 'programme',
            programme_id = dsc_id,
            outcome      = 'cancel',
            trimester    = 2,
            academic_year= 'AY2526',
            is_recurring = False,
        ),
        dict(
            name         = 'Learning Journey (Tri 3)',
            description  = 'DSC Learning Journey — industry visit. All DSC classes cancelled.',
            event_date   = date(2026, 6, 5),     # Week 5 of Tri 3 (assumed Friday)
            is_full_day  = True,
            scope        = 'programme',
            programme_id = dsc_id,
            outcome      = 'cancel',
            trimester    = 3,
            academic_year= 'AY2526',
            is_recurring = False,
        ),
    ]

    for ev_data in events:
        db.session.add(Event(**ev_data))

    db.session.commit()
    print(f"Seeded {len(events)} mandatory events for AY2526.")
    print()
    print("Events added:")
    for ev_data in events:
        scope_label = ev_data['scope'].replace('_', '-')
        print(f"  {ev_data['event_date']}  {ev_data['name']}"
              f"  [{scope_label}, Tri {ev_data['trimester']}]"
              f"  {'(recurring)' if ev_data['is_recurring'] else ''}")
    print()
    print("NOTE: These are assumed sample dates.")
    print("Edit actual dates via Admin > Events once confirmed with the programme office.")
