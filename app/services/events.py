"""Canonical event matching rules.

Every consumer (solver, publication guard, and admin impact preview) must use
these helpers.  Keeping event semantics in one place prevents the UI from
claiming one scope/outcome while generation applies another.
"""

from __future__ import annotations

from datetime import date


def event_date_matches(event, occurrence_date: date) -> bool:
    """Return whether *event* applies on the occurrence's calendar date."""
    if event.is_recurring:
        return (event.event_date.month, event.event_date.day) == (
            occurrence_date.month,
            occurrence_date.day,
        )
    return event.event_date == occurrence_date


def event_scope_matches(event, session) -> bool:
    """Return whether an event's audience includes a class session."""
    if event.scope == 'school_wide':
        return True
    if event.scope == 'programme':
        return bool(
            event.programme_id
            and session.course
            and session.course.programme_id == event.programme_id
        )
    if event.scope == 'course':
        return bool(event.course_id and session.course_id == event.course_id)
    return False


def event_period_matches(event, timeslot_id: int) -> bool:
    """Return whether the full-day/selected-period rule includes a slot."""
    return event.is_full_day or timeslot_id in set(event.blocked_timeslot_ids)


def event_term_matches(event, academic_year: str | None, trimester: int | None) -> bool:
    """Return whether optional academic-year and trimester filters match."""
    if event.academic_year and event.academic_year != academic_year:
        return False
    if event.trimester is not None and event.trimester != trimester:
        return False
    return True


def event_affects_occurrence(
    event,
    session,
    occurrence_date: date,
    timeslot_id: int,
    academic_year: str | None,
    trimester: int | None,
) -> bool:
    """Return True only when every event dimension matches the occurrence."""
    return (
        event_date_matches(event, occurrence_date)
        and event_term_matches(event, academic_year, trimester)
        and event_scope_matches(event, session)
        and event_period_matches(event, timeslot_id)
    )


def matching_event(events, **occurrence):
    """Return the first event affecting an occurrence, or ``None``."""
    return next(
        (event for event in events if event_affects_occurrence(event, **occurrence)),
        None,
    )
