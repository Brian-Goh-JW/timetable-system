import json
from datetime import datetime

from app import db
from app.models.schedule_version import ScheduleVersion
from app.models.timetable_entry import TimetableEntry


ENTRY_FIELDS = (
    'class_session_id', 'timeslot_id', 'room_id', 'override_professor_id',
    'week_number', 'trimester', 'academic_year', 'is_published',
    'is_manually_edited', 'is_backbone',
)


def _serialize_entries(entries):
    return [
        {field: getattr(entry, field) for field in ENTRY_FIELDS}
        for entry in entries
    ]


def create_schedule_version(trimester, stats=None, user_id=None, source='generation',
                            status='draft', label=None, commit=True):
    entries = TimetableEntry.query.filter_by(
        trimester=trimester,
        is_backbone=False,
    ).order_by(TimetableEntry.week_number, TimetableEntry.class_session_id).all()
    if not entries:
        return None
    version = ScheduleVersion(
        trimester=trimester,
        label=label or f'{trimester} {source.title()} {datetime.now():%Y-%m-%d %H:%M}',
        status=status,
        source=source,
        entries_json=json.dumps(_serialize_entries(entries)),
        stats_json=json.dumps(stats or {}, default=str),
        created_by=user_id,
    )
    db.session.add(version)
    if status == 'published':
        ScheduleVersion.query.filter_by(trimester=trimester, status='published').update(
            {'status': 'archived'}, synchronize_session=False
        )
        version.status = 'published'
    if commit:
        db.session.commit()
    return version


def restore_schedule_version(version, publish=False):
    rows = json.loads(version.entries_json)
    TimetableEntry.query.filter_by(
        trimester=version.trimester,
        is_backbone=False,
    ).delete(synchronize_session=False)
    for row in rows:
        row['is_published'] = bool(publish)
        row['is_backbone'] = False
        db.session.add(TimetableEntry(**row))
    if publish:
        TimetableEntry.query.filter_by(trimester=version.trimester, is_backbone=True).update(
            {'is_published': False}, synchronize_session=False
        )
        ScheduleVersion.query.filter_by(
            trimester=version.trimester,
            status='published',
        ).update({'status': 'archived'}, synchronize_session=False)
        version.status = 'published'
    db.session.commit()
    return len(rows)


def version_summary(version):
    rows = json.loads(version.entries_json)
    return {
        'entries': len(rows),
        'sessions': len({row['class_session_id'] for row in rows}),
        'rooms': len({row['room_id'] for row in rows if row['room_id']}),
        'published_entries': sum(1 for row in rows if row['is_published']),
    }


def compare_versions(left, right):
    def assignment_map(version):
        return {
            (row['class_session_id'], row['week_number']): (
                row['timeslot_id'], row['room_id'], row['override_professor_id']
            )
            for row in json.loads(version.entries_json)
        }
    left_rows = assignment_map(left)
    right_rows = assignment_map(right)
    keys = set(left_rows) | set(right_rows)
    return {
        'unchanged': sum(left_rows.get(key) == right_rows.get(key) for key in keys),
        'changed': sum(
            key in left_rows and key in right_rows and left_rows[key] != right_rows[key]
            for key in keys
        ),
        'added': sum(key not in left_rows for key in keys),
        'removed': sum(key not in right_rows for key in keys),
    }
