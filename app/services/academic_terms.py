"""Database-backed academic-term calendar helpers."""

from datetime import timedelta
import holidays as holidays_lib

from app import db
from app.models.academic_calendar import AcademicCalendar


DEFAULT_TERM_STARTS = {
    'AY2425': {1: '2024-09-02', 2: '2025-01-06', 3: '2025-05-05'},
    'AY2526': {1: '2025-09-01', 2: '2026-01-05', 3: '2026-05-04'},
    'AY2627': {1: '2026-08-31', 2: '2027-01-04', 3: '2027-05-03'},
}


def term_key(academic_year, trimester):
    return f'{academic_year}-T{int(trimester)}'


def known_start_date(academic_year, trimester):
    if not academic_year or trimester not in (1, 2, 3):
        return ''
    first_week = AcademicCalendar.query.filter_by(
        trimester=term_key(academic_year, trimester), week_number=1
    ).first()
    if first_week:
        return first_week.start_date.isoformat()
    return DEFAULT_TERM_STARTS.get(academic_year, {}).get(trimester, '')


def configured_terms():
    return (
        AcademicCalendar.query.filter_by(week_number=1)
        .order_by(AcademicCalendar.trimester.desc()).all()
    )


def configure_term(academic_year, trimester, start_date, break_weeks):
    """Replace one term's 13 teaching-week rows atomically."""
    key = term_key(academic_year, trimester)
    break_weeks = {int(value) for value in break_weeks}
    AcademicCalendar.query.filter_by(trimester=key).delete(
        synchronize_session=False
    )
    current = start_date
    singapore_holidays = holidays_lib.Singapore(
        years={start_date.year, (start_date + timedelta(weeks=13)).year}
    )
    for calendar_week in range(1, 14):
        weekdays = [current + timedelta(days=offset) for offset in range(5)]
        holiday_names = [singapore_holidays.get(day) for day in weekdays if day in singapore_holidays]
        notes = []
        if calendar_week in break_weeks:
            notes.append('Term break')
        if holiday_names:
            notes.append('Public holiday: ' + ', '.join(holiday_names))
        db.session.add(AcademicCalendar(
            trimester=key,
            week_number=calendar_week,
            start_date=current,
            end_date=current + timedelta(days=4),
            is_term_break=calendar_week in break_weeks,
            is_public_holiday=bool(holiday_names),
            notes='; '.join(notes) if notes else None,
        ))
        current += timedelta(days=7)
    db.session.commit()
    return key
