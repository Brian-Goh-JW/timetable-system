"""
Pre-solve readiness checker.
Returns (blockers, warnings) - only blockers prevent generation.
The solver can still schedule sessions with no professor (blank staff field),
but every synchronous session must have a student group so it appears in the
student timetable and is included in group constraints.
"""

from app.models.course import Course
from app.models.class_session import ClassSession
from app.models.timeslot import TimeSlot


def get_blocking_issues(trimester_num=None):
    """
    Returns (blockers: list[str], warnings: list[str]).
    Empty blockers list means the system is ready to schedule.

    Args:
        trimester_num : int|None - if provided, only check sessions for that trimester.
    """
    from sqlalchemy import exists
    from app.models.class_session_professor import ClassSessionProfessor

    blockers = []
    warnings = []

    def session_base():
        # Deferred sessions (deferred_from_solve=True) are never solved or
        # exported (solver.py and the Template 2 export route both filter
        # them out) - excluding them here too keeps these warnings scoped to
        # what will actually appear in the output, instead of flagging gaps
        # in sessions the admin can't even see on the export.
        q = ClassSession.query.filter(ClassSession.deferred_from_solve.is_(False))
        if trimester_num is not None:
            q = q.filter(ClassSession.trimester == trimester_num)
        return q

    # 1. F2f/hybrid courses with no split count AND no sessions yet
    missing_split_query = Course.query.filter(
        Course.delivery_mode.in_(['f2f', 'hybrid']),
        Course.split_count.is_(None)
    )
    if trimester_num is not None:
        missing_split_query = missing_split_query.filter(Course.trimester == trimester_num)
    missing_split = missing_split_query.all()
    for c in missing_split:
        if not c.class_sessions:
            blockers.append(f'{c.module_code}: no sessions and split count not set.')

    # 2. Sessions with no professor - still scheduled (room + time), just
    # exported with a blank staff field - warning only, not a blocker
    no_prof = session_base().filter(
        ~exists().where(ClassSessionProfessor.session_id == ClassSession.id)
    ).all()
    if no_prof:
        warnings.append(
            f'{len(no_prof)} session(s) have no professor assigned - still scheduled, but the '
            f'timetable and Template 2 export will show a blank staff field until one is assigned.'
        )

    # 3. Synchronous sessions with no student group cannot be displayed to
    # students or checked for group conflicts, so generation must stop.
    no_group = session_base().filter(
        ClassSession.is_async.is_(False),
        ClassSession.student_group_id.is_(None),
    ).all()
    if no_group:
        blockers.append(
            f'{len(no_group)} synchronous session(s) have no student group assigned.'
        )

    # 4. Fixed timeslot incompatibilities - these would make the model infeasible (blocker)
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

    # 5. Assessment load - no single module's class may have more than 1 quiz in
    # the same teaching week (per Ms. Yang's requirements doc, a hard constraint -
    # confirmed with Brian on 2026-07-10 that "class" here means one module's own
    # class, not a student group's full set of modules). This can't be fixed by
    # the solver - teaching_weeks is fixed input data, not something the solver
    # decides - so it's surfaced here as a blocker instead of a solver rule.
    from collections import defaultdict
    from app.engine.solver import _weeks_overlap

    # Grouped by (course, section) rather than course alone - a module split
    # into unlabelled parallel sections (e.g. EPE2300's Group A/B) is two
    # separate classes, not one class with two quizzes.
    quizzes_by_course_section = defaultdict(list)
    for s in session_base().filter(ClassSession.session_type == 'quiz').all():
        quizzes_by_course_section[(s.course_id, s.group_label)].append(s)

    for (course_id, section), quiz_list in quizzes_by_course_section.items():
        for i in range(len(quiz_list)):
            for j in range(i + 1, len(quiz_list)):
                qi, qj = quiz_list[i], quiz_list[j]
                if _weeks_overlap(qi, qj):
                    grp_label = qi.student_group.group_label if qi.student_group else '?'
                    section_note = f' (section {section})' if section and section != 'All' else ''
                    blockers.append(
                        f'{qi.course.module_code}{section_note} has 2 quiz sessions both falling in an '
                        f'overlapping teaching week for group {grp_label} - max 1 quiz/week allowed.'
                    )

    return blockers, warnings
