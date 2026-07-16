"""
STEP 45 — Schema addition: solve_runs.updated_at.

Found 2026-07-16: the Scheduling Report always showed "Generated <date>"
using created_at, but SolveRun is upserted in place on every regenerate -
created_at only reflects the FIRST time that trimester was ever generated,
so the displayed date silently went stale after the first regenerate even
though the stats underneath were current. Adds a real updated_at column,
set on every save (see _save_solve_run in app/routes/admin.py), and
backfills existing rows to their current created_at as a reasonable
starting value.

Run ONCE:
    python bootstrap/45_schema_solve_run_updated_at.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from sqlalchemy import text, inspect

app = create_app()

with app.app_context():
    inspector = inspect(db.engine)
    existing_cols = {c['name'] for c in inspector.get_columns('solve_runs')}
    if 'updated_at' not in existing_cols:
        db.session.execute(text(
            'ALTER TABLE solve_runs ADD COLUMN updated_at DATETIME NULL'
        ))
        db.session.commit()
        print('  [OK] solve_runs.updated_at column added')
    else:
        print('  [OK] solve_runs.updated_at already exists - skipped')

    db.session.execute(text(
        'UPDATE solve_runs SET updated_at = created_at WHERE updated_at IS NULL'
    ))
    db.session.commit()
    print('  [OK] backfilled updated_at = created_at for existing rows')
    print('\nMigration complete.')
