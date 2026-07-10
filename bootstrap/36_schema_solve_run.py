"""
STEP 36 — Schema addition for persisted solve results.

Adds:
  - solve_runs table (new — via db.create_all())

One row per trimester, upserted after every successful generation, so the
"How This Was Generated" summary and the Scheduling Administration Report
survive server restarts and don't depend on which process ran the solve.

Run ONCE:
    python bootstrap/36_schema_solve_run.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models.solve_run import SolveRun  # noqa: F401 — registers table

app = create_app()

with app.app_context():
    db.create_all()  # only creates missing tables (solve_runs) — safe to re-run
    print('  [OK] solve_runs table created (or already existed)')
    print('\nMigration complete.')
