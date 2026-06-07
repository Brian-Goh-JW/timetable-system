"""
Pre-solve readiness checker.
Returns a list of blocking issues that must be resolved before the solver can run.
"""

from app.models.course import Course
from app.models.class_session import ClassSession
from app.models.timeslot import TimeSlot


def get_blocking_issues(trimester_num=None):
    """
    Returns a list of human-readable issue strings.
    Empty list means the system is ready to schedule.

    Args:
        trimester_num : int|None — if provided, only check sessions for that trimester (1/2/3).
                                   If None, check all sessions.
    """
    from sqlalchemy import exists
    from app.models.class_session_professor import ClassSessionProfessor

    issues = []

    # Build a base session filter (optionally scoped to one trimester)
    def session_base():
        q = ClassSession.query
        if trimester_num is not None:
            q = q.filter(ClassSession.trimester == trimester_num)
        return q

    # 1. F2f / hybrid courses with no split count AND no sessions yet
    #    (If sessions are already loaded from Excel, split_count is not needed)
    missing_split = Course.query.filter(
        Course.delivery_mode.in_(['f2f', 'hybrid']),
        Course.split_count.is_(None)
    ).all()
    for c in missing_split:
        if not c.class_sessions:
            issues.append(f'{c.module_code}: no sessions and split count not set.')

    # 2. Sessions with no professor assigned (check junction table)
    no_prof = session_base().filter(
        ~exists().where(ClassSessionProfessor.session_id == ClassSession.id)
    ).all()
    for s in no_prof:
        issues.append(
            f'{s.course.module_code} ({s.session_type}, {s.delivery_mode}): '
            f'no professor assigned.'
        )

    # 3. F2f sessions with no student group assigned
    no_group = session_base().filter(
        ClassSession.delivery_mode == 'f2f',
        ClassSession.student_group_id.is_(None)
    ).all()
    for s in no_group:
        issues.append(
            f'{s.course.module_code} ({s.session_type}, f2f): '
            f'no student group assigned.'
        )

    # 4. Sessions with a fixed timeslot that is incompatible (wrong duration or type)
    fixed_sessions = session_base().filter(
        ClassSession.fixed_timeslot_id.isnot(None)
    ).all()
    for s in fixed_sessions:
        ts = TimeSlot.query.get(s.fixed_timeslot_id)
        if not ts:
            issues.append(
                f'{s.course.module_code} ({s.session_type}): '
                f'fixed timeslot no longer exists. Clear the fixed slot and re-save.'
            )
            continue

        # Check duration match
        start_m    = ts.start_time.hour * 60 + ts.start_time.minute
        end_m      = ts.end_time.hour   * 60 + ts.end_time.minute
        slot_hours = (end_m - start_m) // 60
        if slot_hours != s.duration_hours:
            issues.append(
                f'{s.course.module_code} ({s.session_type}): fixed slot '
                f'{ts.day_of_week} {ts.period_label} is {slot_hours}h '
                f'but session requires {s.duration_hours}h.'
            )

        # Check lab / non-lab type match
        is_lab_slot    = ts.period_label.startswith('Lab')
        is_lab_session = (s.session_type == 'lab')
        if is_lab_slot != is_lab_session:
            slot_kind    = 'a lab slot' if is_lab_slot else 'a non-lab slot'
            session_kind = 'a lab session' if is_lab_session else 'a non-lab session'
            issues.append(
                f'{s.course.module_code} ({s.session_type}): fixed slot '
                f'{ts.day_of_week} {ts.period_label} is {slot_kind} '
                f'but the session is {session_kind}.'
            )

    return issues
