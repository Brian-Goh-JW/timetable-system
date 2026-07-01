"""
Pre-solve readiness checker.
Returns (blockers, warnings) — only blockers prevent generation.
The solver gracefully skips sessions with no professor or no student group,
so those are warnings only.
"""

from app.models.course import Course
from app.models.class_session import ClassSession
from app.models.timeslot import TimeSlot


def get_blocking_issues(trimester_num=None):
    """
    Returns (blockers: list[str], warnings: list[str]).
    Empty blockers list means the system is ready to schedule.

    Args:
        trimester_num : int|None — if provided, only check sessions for that trimester.
    """
    from sqlalchemy import exists
    from app.models.class_session_professor import ClassSessionProfessor

    blockers = []
    warnings = []

    def session_base():
        q = ClassSession.query
        if trimester_num is not None:
            q = q.filter(ClassSession.trimester == trimester_num)
        return q

    # 1. F2f/hybrid courses with no split count AND no sessions yet
    missing_split = Course.query.filter(
        Course.delivery_mode.in_(['f2f', 'hybrid']),
        Course.split_count.is_(None)
    ).all()
    for c in missing_split:
        if not c.class_sessions:
            blockers.append(f'{c.module_code}: no sessions and split count not set.')

    # 2. Sessions with no professor — solver skips these, so warning only
    no_prof = session_base().filter(
        ~exists().where(ClassSessionProfessor.session_id == ClassSession.id)
    ).all()
    if no_prof:
        warnings.append(
            f'{len(no_prof)} session(s) have no professor assigned and will be skipped by the solver.'
        )

    # 3. F2f sessions with no student group — solver skips these, so warning only
    no_group = session_base().filter(
        ClassSession.delivery_mode == 'f2f',
        ClassSession.student_group_id.is_(None),
        exists().where(ClassSessionProfessor.session_id == ClassSession.id),
    ).all()
    if no_group:
        warnings.append(
            f'{len(no_group)} f2f session(s) have no student group assigned and will be skipped.'
        )

    # 4. Fixed timeslot incompatibilities — these would make the model infeasible (blocker)
    fixed_sessions = session_base().filter(
        ClassSession.fixed_timeslot_id.isnot(None)
    ).all()
    for s in fixed_sessions:
        ts = TimeSlot.query.get(s.fixed_timeslot_id)
        if not ts:
            blockers.append(
                f'{s.course.module_code} ({s.session_type}): '
                f'fixed timeslot no longer exists. Clear the fixed slot and re-save.'
            )
            continue
        start_m    = ts.start_time.hour * 60 + ts.start_time.minute
        end_m      = ts.end_time.hour   * 60 + ts.end_time.minute
        slot_hours = (end_m - start_m) // 60
        if slot_hours != s.duration_hours:
            blockers.append(
                f'{s.course.module_code} ({s.session_type}): fixed slot '
                f'{ts.day_of_week} {ts.period_label} is {slot_hours}h '
                f'but session requires {s.duration_hours}h.'
            )

    return blockers, warnings
