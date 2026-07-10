"""
STEP 27 — Schema expansion for cross-programme shared modules.

Adds:
  - shared_module_groups table (new — via db.create_all())
  - class_sessions.shared_module_group_id FK column

Run ONCE:
    python bootstrap/27_schema_shared_modules.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models.shared_module_group import SharedModuleGroup  # noqa: F401 — registers table

app = create_app()

with app.app_context():
    db.create_all()  # only creates missing tables (shared_module_groups) — safe to re-run
    print('  [OK] shared_module_groups table created (or already existed)')

    try:
        db.session.execute(db.text(
            """ALTER TABLE class_sessions
               ADD COLUMN shared_module_group_id INT NULL,
               ADD CONSTRAINT fk_cs_shared_module_group
               FOREIGN KEY (shared_module_group_id) REFERENCES shared_module_groups(id)
               ON DELETE SET NULL"""
        ))
        db.session.commit()
        print('  [OK] class_sessions.shared_module_group_id added')
    except Exception as e:
        db.session.rollback()
        err = str(e)
        if '1060' in err or 'Duplicate column' in err:
            print('  [SKIP] class_sessions.shared_module_group_id — already exists')
        else:
            print(f'  [ERROR] {e}')
            raise

    print('\nMigration complete.')
