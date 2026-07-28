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
from sqlalchemy.engine import URL

from app import create_app, db

def mysql_source_url():
    """Build the source URL without printing or interpolating its password."""
    direct = os.environ.get('MYSQL_URL', '').strip()
    if direct:
        return direct
    required = {
        'MYSQL_HOST': os.environ.get('MYSQL_HOST', '').strip(),
        'MYSQL_USER': os.environ.get('MYSQL_USER', '').strip(),
        'MYSQL_PASSWORD': os.environ.get('MYSQL_PASSWORD', ''),
        'MYSQL_DATABASE': os.environ.get('MYSQL_DATABASE', '').strip(),
    }
    if not all(required.values()):
        return None
    return URL.create(
        'mysql+pymysql',
        username=required['MYSQL_USER'],
        password=required['MYSQL_PASSWORD'],
        host=required['MYSQL_HOST'],
        port=int(os.environ.get('MYSQL_PORT', '3306')),
        database=required['MYSQL_DATABASE'],
        query={'charset': 'utf8mb4'},
    )

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DB_DIR = os.path.join(BASE_DIR, 'database')
SQLITE_PATH = os.path.join(DB_DIR, 'timetable.db')
SQLITE_URL = 'sqlite:///' + SQLITE_PATH.replace('\\', '/')


def main():
    source_url = mysql_source_url()
    if not source_url:
        print('! Set MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, and MYSQL_DATABASE first.')
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

        source = create_engine(source_url)
        target = create_engine(SQLITE_URL)

        print('Source (MySQL) : configured securely through environment variables')
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
