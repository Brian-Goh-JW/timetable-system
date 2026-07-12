"""
STEP 38 — Schema addition for a course's official (SIT catalog) year range.

Adds:
  - courses.official_year_range (new nullable column)

Display-only reference field (e.g. "Year 2-4") - a module's scheduling
year_level (which cohort it's actually taught to in this system) can
legitimately differ from its official catalog span, since a module might
be offered to Year 1 in this system but sit in SIT's catalog as spanning
Year 1-3. This field is never read by the solver; it's admin-entered from
SIT's own website, disclosed as self-input reference data on System Info.
Blank for every course until an admin fills it in - there's no automated
source for this data.

Run ONCE:
    python bootstrap/38_schema_course_year_range.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from sqlalchemy import text, inspect

app = create_app()

with app.app_context():
    inspector = inspect(db.engine)
    existing_cols = {c['name'] for c in inspector.get_columns('courses')}
    if 'official_year_range' not in existing_cols:
        db.session.execute(text(
            'ALTER TABLE courses ADD COLUMN official_year_range VARCHAR(20) NULL'
        ))
        db.session.commit()
        print('  [OK] courses.official_year_range column added')
    else:
        print('  [OK] courses.official_year_range already exists - skipped')
    print('\nMigration complete.')
