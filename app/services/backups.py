"""Recoverable local database backups."""

from datetime import datetime, timezone
from pathlib import Path
import shutil


def create_sqlite_backup(app, keep=14):
    uri = str(app.config.get('SQLALCHEMY_DATABASE_URI', ''))
    if not uri.startswith('sqlite:///') or uri.endswith(':memory:'):
        return None
    source = Path(uri.removeprefix('sqlite:///'))
    if not source.exists():
        return None
    backup_dir = source.parent / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = backup_dir / f'{source.stem}-{timestamp}{source.suffix}'
    shutil.copy2(source, target)
    backups = sorted(
        backup_dir.glob(f'{source.stem}-*{source.suffix}'),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old_backup in backups[max(1, int(keep)):]:
        old_backup.unlink()
    return target
