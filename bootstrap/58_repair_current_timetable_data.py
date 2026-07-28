"""Repair semantic timetable data after importing the legacy database.

Idempotent. Run only after taking a database backup. It preserves courses and
sessions, removes invalid pins/occurrences, rebuilds the 13-week calendars, and
publishes only generated layers that pass the same hard audit as the UI.
"""

import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.engine.solver import (
    _get_or_create_calendar,
    _get_sg_holidays,
    _room_compatible,
)
from app.models.academic_calendar import AcademicCalendar
from app.models.class_session import ClassSession
from app.models.event import Event
from app.models.flag_response import FlagResponse
from app.models.solve_run import SolveRun
from app.models.student_group import StudentGroup
from app.models.timetable_entry import TimetableEntry
from app.models.timetable_flag import TimetableFlag
from app.models.user import User
from app.routes.admin import _audit_generated_hard_conflicts


CALENDAR_STARTS = {
    'AY2526-T1': date(2025, 9, 1),
    'AY2526-T2': date(2026, 1, 5),
    'AY2526-T3': date(2026, 5, 4),
}


def _delete_entries(entries):
    entry_ids = [entry.id for entry in entries]
    if not entry_ids:
        return 0
    flag_ids = [
        row[0] for row in db.session.query(TimetableFlag.id).filter(
            TimetableFlag.timetable_entry_id.in_(entry_ids)
        ).all()
    ]
    if flag_ids:
        FlagResponse.query.filter(FlagResponse.flag_id.in_(flag_ids)).delete(
            synchronize_session=False
        )
        TimetableFlag.query.filter(TimetableFlag.id.in_(flag_ids)).delete(
            synchronize_session=False
        )
    TimetableEntry.query.filter(TimetableEntry.id.in_(entry_ids)).delete(
        synchronize_session=False
    )
    return len(entry_ids)


def _invalid_generated_occurrences(entries):
    """Legacy generated rows that violate calendar/teaching-week rules."""
    entries = list(entries)
    trimesters = {entry.trimester for entry in entries}
    calendars = AcademicCalendar.query.filter(
        AcademicCalendar.trimester.in_(trimesters)
    ).all() if trimesters else []
    calendar_by_key = {
        (calendar.trimester, calendar.week_number): calendar
        for calendar in calendars
    }
    holidays_by_trimester = {}
    for trimester in trimesters:
        active = [
            calendar for calendar in calendars
            if calendar.trimester == trimester and not calendar.is_term_break
        ]
        holidays_by_trimester[trimester] = (
            _get_sg_holidays(
                min(calendar.start_date for calendar in active),
                max(calendar.end_date for calendar in active),
            ) if active else set()
        )
    cancel_events = Event.query.filter(Event.outcome == 'cancel').all()
    day_offset = {
        'Monday': 0, 'Tuesday': 1, 'Wednesday': 2,
        'Thursday': 3, 'Friday': 4,
    }
    seen_session_weeks = set()
    invalid = []
    for entry in entries:
        session = entry.class_session
        calendar = calendar_by_key.get((entry.trimester, entry.week_number))
        bad = calendar is None or calendar.is_term_break

        if session.teaching_weeks:
            allowed = {
                int(part.strip()) for part in session.teaching_weeks.split(',')
                if part.strip().isdigit()
            }
            bad = bad or entry.week_number not in allowed

        if not bad and entry.timeslot.day_of_week in day_offset:
            session_date = calendar.start_date + timedelta(
                days=day_offset[entry.timeslot.day_of_week]
            )
            bad = session_date in holidays_by_trimester.get(entry.trimester, set())
            if not bad:
                for event in cancel_events:
                    if event.event_date != session_date:
                        continue
                    if event.trimester is not None and event.trimester != session.trimester:
                        continue
                    if (event.academic_year is not None
                            and event.academic_year != entry.academic_year):
                        continue
                    blocked_slots = set(event.blocked_timeslot_ids)
                    if event.is_full_day or entry.timeslot_id in blocked_slots:
                        bad = True
                        break

        key = (entry.trimester, entry.class_session_id, entry.week_number)
        if not bad:
            if key in seen_session_weeks:
                bad = True
            else:
                seen_session_weeks.add(key)
        if bad:
            invalid.append(entry)
    return invalid


def main():
    app = create_app()
    report = {
        'teaching_weeks_normalized': 0,
        'sessions_deferred_invalid_weeks': 0,
        'invalid_fixed_room_pins_cleared': 0,
        'out_of_range_entries_removed': 0,
        'invalid_calendar_occurrences_removed': 0,
        'conflicting_generated_rows_removed': {},
        'layers_published': [],
        'layers_unpublished': [],
        'calendars_rebuilt': [],
    }

    with app.app_context():
        for session in ClassSession.query.all():
            if session.teaching_weeks:
                valid = []
                for part in session.teaching_weeks.split(','):
                    part = part.strip()
                    if part.isdigit() and 1 <= int(part) <= 13:
                        valid.append(int(part))
                normalized = ','.join(str(week) for week in sorted(set(valid)))
                if normalized:
                    if normalized != session.teaching_weeks:
                        session.teaching_weeks = normalized
                        report['teaching_weeks_normalized'] += 1
                else:
                    session.deferred_from_solve = True
                    report['sessions_deferred_invalid_weeks'] += 1

            if session.fixed_room_id:
                room = session.fixed_room
                if room is None or not room.is_active or not _room_compatible(room, session):
                    session.fixed_room_id = None
                    report['invalid_fixed_room_pins_cleared'] += 1

        invalid_entries = TimetableEntry.query.filter(
            db.or_(TimetableEntry.week_number < 1, TimetableEntry.week_number > 13)
        ).all()
        report['out_of_range_entries_removed'] = _delete_entries(invalid_entries)
        db.session.commit()

        for trimester, start in CALENDAR_STARTS.items():
            _get_or_create_calendar(trimester, start, {7})
            report['calendars_rebuilt'].append(trimester)

        # Remove stale audit-only calendar artefacts when they have no entries.
        for trimester, in db.session.query(AcademicCalendar.trimester).filter(
            AcademicCalendar.trimester.like('%BRIDGETEST%')
        ).distinct().all():
            if not TimetableEntry.query.filter_by(trimester=trimester).first():
                AcademicCalendar.query.filter_by(trimester=trimester).delete(
                    synchronize_session=False
                )

        trimesters = [
            row[0] for row in db.session.query(TimetableEntry.trimester).distinct().all()
        ]
        for trimester in trimesters:
            generated = TimetableEntry.query.filter_by(
                trimester=trimester, is_backbone=False
            ).all()
            if generated:
                invalid_occurrences = _invalid_generated_occurrences(generated)
                report['invalid_calendar_occurrences_removed'] += _delete_entries(
                    invalid_occurrences
                )
                if invalid_occurrences:
                    db.session.flush()
                    generated = TimetableEntry.query.filter_by(
                        trimester=trimester, is_backbone=False
                    ).all()
                conflicts = _audit_generated_hard_conflicts(generated)
                if conflicts:
                    report['conflicting_generated_rows_removed'][trimester] = _delete_entries(
                        generated
                    )
                    SolveRun.query.filter_by(trimester=trimester).delete(
                        synchronize_session=False
                    )
                    generated = []

            # Imported backbone remains available to administrators as source
            # evidence, but it must never stay visible when it fails hard rules.
            backbone = TimetableEntry.query.filter_by(
                trimester=trimester, is_backbone=True
            ).all()
            if backbone and _audit_generated_hard_conflicts(backbone):
                TimetableEntry.query.filter_by(
                    trimester=trimester, is_backbone=True
                ).update({'is_published': False}, synchronize_session=False)
                report['layers_unpublished'].append(f'{trimester}:backbone')

            if generated and not _audit_generated_hard_conflicts(generated):
                TimetableEntry.query.filter_by(trimester=trimester).update(
                    {'is_published': False}, synchronize_session=False
                )
                TimetableEntry.query.filter_by(
                    trimester=trimester, is_backbone=False
                ).update({'is_published': True}, synchronize_session=False)
                report['layers_published'].append(f'{trimester}:generated')

        student = User.query.filter_by(email='student@sit.edu.sg', role='student').first()
        dsc_group = StudentGroup.query.filter_by(group_label='DSC-Y1').first()
        if student and dsc_group:
            student.student_group_id = dsc_group.id
            student.set_password('Test1234')

        db.session.commit()
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
