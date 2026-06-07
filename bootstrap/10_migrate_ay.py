"""
Migration 10 — Add academic_year column to timetable_entries.

Run once:
    python bootstrap/10_migrate_ay.py
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from sqlalchemy import text, inspect

app = create_app()
with app.app_context():
    inspector = inspect(db.engine)
    columns = [c['name'] for c in inspector.get_columns('timetable_entries')]

    if 'academic_year' in columns:
        print('✓ academic_year column already exists — nothing to do.')
    else:
        with db.engine.connect() as conn:
            conn.execute(text("""
                ALTER TABLE timetable_entries
                ADD COLUMN academic_year VARCHAR(10) NULL
                AFTER trimester
            """))
            conn.commit()
        print('✓ Added academic_year to timetable_entries')

    print('Done.')
