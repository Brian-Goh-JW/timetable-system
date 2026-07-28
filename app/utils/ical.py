"""Standards-based calendar export without sending timetable data elsewhere."""

from datetime import datetime, timedelta, timezone


DAY_OFFSET = {
    'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3,
    'Friday': 4, 'Saturday': 5, 'Sunday': 6,
}


def _escape(value):
    return str(value or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


def build_ical(entries, calendar_weeks, calendar_name):
    weeks = {week.week_number: week for week in calendar_weeks}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = [
        'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//SIT Timetable//EN',
        'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
        f'X-WR-CALNAME:{_escape(calendar_name)}', 'X-WR-TIMEZONE:Asia/Singapore',
    ]
    for entry in sorted(entries, key=lambda row: (row.week_number, row.timeslot.start_time)):
        week = weeks.get(entry.week_number)
        if week is None or entry.timeslot.day_of_week not in DAY_OFFSET:
            continue
        event_date = week.start_date + timedelta(days=DAY_OFFSET[entry.timeslot.day_of_week])
        start = datetime.combine(event_date, entry.timeslot.start_time)
        end = datetime.combine(event_date, entry.timeslot.end_time)
        session = entry.class_session
        room = entry.room.room_code if entry.room else 'Online'
        teachers = ', '.join(
            professor.user.name for professor in session.all_professors if professor.user
        )
        lines.extend([
            'BEGIN:VEVENT',
            f'UID:entry-{entry.id}@sit-timetable.local',
            f'DTSTAMP:{stamp}',
            f'DTSTART;TZID=Asia/Singapore:{start.strftime("%Y%m%dT%H%M%S")}',
            f'DTEND;TZID=Asia/Singapore:{end.strftime("%Y%m%dT%H%M%S")}',
            f'SUMMARY:{_escape(session.course.module_code + " " + session.session_type.title())}',
            f'LOCATION:{_escape(room)}',
            f'DESCRIPTION:{_escape(teachers)}',
            'END:VEVENT',
        ])
    lines.append('END:VCALENDAR')
    return '\r\n'.join(lines) + '\r\n'
