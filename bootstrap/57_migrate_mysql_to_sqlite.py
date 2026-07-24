"""Migrate the whole database from MySQL into a local SQLite file.

Reads from the current MySQL database and writes an identical copy into
database/timetable.db, preserving every primary key so all foreign key
relationships stay intact. MySQL is only ever read from, never modified,
so it remains available as a fallback until the SQLite copy is verified.

Run once:  python bootstrap/57_migrate_mysql_to_sqlite.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine

from app import create_app, db

# Source: the existing MySQL database. Supplied through MYSQL_URL so no
# credentials are stored in this file, for example:
#   $env:MYSQL_URL = 'mysql+pymysql://root:yourpassword@127.0.0.1/timetable_db'
MYSQL_URL = os.environ.get('MYSQL_URL')

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DB_DIR = os.path.join(BASE_DIR, 'database')
SQLITE_PATH = os.path.join(DB_DIR, 'timetable.db')
SQLITE_URL = 'sqlite:///' + SQLITE_PATH.replace('\\', '/')


def main():
    if not MYSQL_URL:
        print('! Set MYSQL_URL to the source MySQL database first, e.g.')
        print("  $env:MYSQL_URL = 'mysql+pymysql://root:PASSWORD@127.0.0.1/timetable_db'")
        return 1

    os.makedirs(DB_DIR, exist_ok=True)

    if os.path.exists(SQLITE_PATH):
        print(f'! {SQLITE_PATH} already exists.')
        print('  Delete it first if you want a clean re-migration. Aborting.')
        return 1

    # The app is only used to get the model metadata; we talk to both
    # databases through explicit engines rather than the app session.
    app = create_app()
    with app.app_context():
        metadata = db.metadata

        source = create_engine(MYSQL_URL)
        target = create_engine(SQLITE_URL)

        print(f'Source (MySQL) : {MYSQL_URL.split("@")[-1]}')
        print(f'Target (SQLite): {SQLITE_PATH}\n')

        # Build the full schema in SQLite from the model definitions.
        metadata.create_all(target)
        print(f'Created {len(metadata.sorted_tables)} tables in SQLite.\n')

        copied = {}
        # sorted_tables is topologically ordered (parents before children),
        # so foreign keys always resolve as we insert.
        with source.connect() as src_conn, target.begin() as tgt_conn:
            for table in metadata.sorted_tables:
                rows = src_conn.execute(table.select()).fetchall()
                if rows:
                    payload = [dict(r._mapping) for r in rows]
                    tgt_conn.execute(table.insert(), payload)
                copied[table.name] = len(rows)
                print(f'  {table.name:<32} {len(rows):>6} rows')

        # Verify: re-count both sides independently and compare.
        print('\nVerifying row counts...')
        mismatches = []
        with source.connect() as src_conn, target.connect() as tgt_conn:
            for table in metadata.sorted_tables:
                s = src_conn.execute(
                    db.text(f'SELECT COUNT(*) FROM {table.name}')
                ).scalar()
                t = tgt_conn.execute(
                    db.text(f'SELECT COUNT(*) FROM {table.name}')
                ).scalar()
                if s != t:
                    mismatches.append((table.name, s, t))

        if mismatches:
            print('\nMIGRATION FAILED - row counts do not match:')
            for name, s, t in mismatches:
                print(f'  {name}: MySQL={s} SQLite={t}')
            return 1

        total = sum(copied.values())
        print(f'\nAll {len(copied)} tables match. {total} rows migrated successfully.')
        print(f'SQLite database written to: {SQLITE_PATH}')
        return 0


if __name__ == '__main__':
    sys.exit(main())
