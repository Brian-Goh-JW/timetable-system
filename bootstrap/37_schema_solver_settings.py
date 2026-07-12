"""
STEP 37 — Schema addition for admin-adjustable soft constraint settings.

Adds:
  - solver_settings table (new — via db.create_all())

One row per soft constraint an admin has actually changed (enabled/disabled
or given a custom priority weight) via the Admin Tools > Constraint Settings
page. No row for a constraint_id means "use solver.py's default weight,
enabled" — nothing needs seeding, the solver falls back to its own module
constants for anything not overridden here.

Run ONCE:
    python bootstrap/37_schema_solver_settings.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models.solver_setting import SolverSetting  # noqa: F401 — registers table

app = create_app()

with app.app_context():
    db.create_all()  # only creates missing tables (solver_settings) — safe to re-run
    print('  [OK] solver_settings table created (or already existed)')
    print('\nMigration complete.')
