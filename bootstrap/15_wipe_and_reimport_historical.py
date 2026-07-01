"""
Bootstrap 15 — Wipe AY2526 + AY2627 timetable entries and academic calendars.
Run this BEFORE bootstrap/12 to get a clean slate.

Usage:
    python bootstrap/15_wipe_and_reimport_historical.py
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models.timetable_entry import TimetableEntry
from app.models.academic_calendar import AcademicCalendar

app = create_app()
with app.app_context():
    for ay in ('AY2526', 'AY2627'):
        count = TimetableEntry.query.filter_by(academic_year=ay).count()
        TimetableEntry.query.filter_by(academic_year=ay).delete()
        print(f'Deleted {count} timetable entries for {ay}')

    tri_keys = [
        'AY2526-T1', 'AY2526-T2', 'AY2526-T3',
        'AY2627-T1', 'AY2627-T2', 'AY2627-T3',
    ]
    for tk in tri_keys:
        count = AcademicCalendar.query.filter_by(trimester=tk).count()
        if count:
            AcademicCalendar.query.filter_by(trimester=tk).delete()
            print(f'Deleted {count} calendar weeks for {tk}')

    db.session.commit()
    print('\nDone. Now run: python bootstrap/12_load_historical_timetable.py')
