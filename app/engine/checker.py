"""
Pre-solve readiness checker.
Returns (blockers, warnings) - only blockers prevent generation.
The solver can still schedule sessions with no professor (blank staff field),
and a course with no sessions at all is only a warning (nothing to place), but
every synchronous session must have a student group so it appears in the
student timetable and is included in group constraints.
"""

from app.models.course import Course
from app.models.class_session import ClassSession
from app.models.timeslot import TimeSlot


def get_fixed_hard_constraint_conflicts(trimester_num=None, programme_ids=None):
    """Return human-readable clashes between hard fixed assignments.

    A pair is blocking only when both sessions have overlapping fixed time
    slots and teaching weeks *and* compete for at least one hard resource:
    a valid fixed room (H1), professor (H2), or related student group (H3).
    Sessions in one SharedModuleGroup are a single jointly taught class and
    therefore are deliberately collapsed rather than reported as a clash.

    Invalid fixed-room pins are not H1 conflicts.  The solver safely drops
    those pins and chooses a compatible room, so reporting them as hard here
    would disagree with generation behaviour.
    """
    from app.engine.solver import (
        _room_compatible,
        _sessions_share_students,
        _timeslots_overlap,
        _weeks_overlap,
    )

    query = (
        ClassSession.query
        .filter(
            ClassSession.deferred_from_solve.is_(False),
            ClassSession.is_async.is_(False),
            ClassSession.fixed_timeslot_id.isnot(None),
        )
        .join(ClassSession.course)
    )
    if trimester_num is not None:
        query = query.filter(ClassSession.trimester == trimester_num)
    if programme_ids is not None:
        query = query.filter(Course.programme_id.in_(programme_ids))

    sessions = query.order_by(ClassSession.id).all()
    conflicts = []

    def _clock(value):
        return value.strftime('%H:%M')

    def _session_name(session):
        return f'{session.course.module_code} ({session.session_type})'

    for index, left in enumerate(sessions):
        left_slot = left.fixed_timeslot
        if left_slot is None:
            continue
        for right in sessions[index + 1:]:
            right_slot = right.fixed_timeslot
            if right_slot is None:
                continue
            # ``trimester_num=None`` is used by the all-trimester readiness
            # panel. Sessions in different trimesters never occur together.
            if left.trimester != right.trimester:
                continue
            if (left.shared_module_group_id is not None
                    and left.shared_module_group_id == right.shared_module_group_id):
                continue
            if not _weeks_overlap(left, right) or not _timeslots_overlap(left_slot, right_slot):
                continue

            hard_reasons = []

            same_valid_room = (
                left.fixed_room_id is not None
                and left.fixed_room_id == right.fixed_room_id
                and left.fixed_room is not None
                and left.fixed_room.is_active
                and _room_compatible(left.fixed_room, left)
                and _room_compatible(right.fixed_room, right)
            )
            if same_valid_room:
                hard_reasons.append(f'H1 same room [{left.fixed_room.room_code}]')

            common_professor_ids = set(left.all_professor_ids) & set(right.all_professor_ids)
            if common_professor_ids:
                names = sorted({
                    professor.user.name
                    for professor in left.all_professors
                    if professor.id in common_professor_ids and professor.user
                })
                hard_reasons.append(
                    'H2 professor ' + (', '.join(names) if names else 'assigned to both sessions')
                )

            if left.student_group_id and right.student_group_id:
                if _sessions_share_students(left, right):
                    group_names = sorted({
                        group.group_label
                        for group in (left.student_group, right.student_group)
                        if group is not None
                    })
                    hard_reasons.append(
                        'H3 student group ' + (' / '.join(group_names) if group_names else 'shared')
                    )

            if not hard_reasons:
                continue

            same_clock = (
                left_slot.day_of_week == right_slot.day_of_week
                and left_slot.start_time == right_slot.start_time
                and left_slot.end_time == right_slot.end_time
            )
            if same_clock:
                fixed_description = (
                    f'both fixed to {left_slot.day_of_week} '
                    f'{_clock(left_slot.start_time)}-{_clock(left_slot.end_time)}'
                )
            else:
                fixed_description = (
                    'fixed to overlapping times '
                    f'{left_slot.day_of_week} {_clock(left_slot.start_time)}-{_clock(left_slot.end_time)} '
                    f'and {right_slot.day_of_week} {_clock(right_slot.start_time)}-{_clock(right_slot.end_time)}'
                )

            conflicts.append(
                f'Hard-constraint clash: {_session_name(left)} and {_session_name(right)} are '
                f'{fixed_description} with overlapping teaching weeks. '
                f'Conflicts: {"; ".join(hard_reasons)}.'
            )

    return conflicts


def get_blocking_issues(trimester_num=None, programme_ids=None):
    """
    Returns (blockers: list[str], warnings: list[str]).
    Empty blockers list means the system is ready to schedule.

    Args:
        trimester_num : int|None - if provided, only check sessions for that trimester.
        programme_ids : iterable[int]|None - optionally scope checks to complete programmes.
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
        if programme_ids is not None:
            q = q.join(ClassSession.course).filter(Course.programme_id.in_(programme_ids))
        return q

    # F2f/hybrid courses with no split count AND no sessions yet.
    # Warning, not a blocker: a course with zero sessions has nothing for the
    # solver to place, so it cannot make the model infeasible - it simply is
    # not scheduled. Surfaced so the admin notices an unconfigured or leftover
    # course, but it never stops the rest of the timetable generating.
    missing_split_query = Course.query.filter(
        Course.delivery_mode.in_(['f2f', 'hybrid']),
        Course.split_count.is_(None)
    )
    if trimester_num is not None:
        missing_split_query = missing_split_query.filter(Course.trimester == trimester_num)
    if programme_ids is not None:
        missing_split_query = missing_split_query.filter(Course.programme_id.in_(programme_ids))
    missing_split = [c for c in missing_split_query.all() if not c.class_sessions]
    if missing_split:
        codes = ', '.join(c.module_code for c in missing_split)
        warnings.append(
            f'{len(missing_split)} course(s) have no sessions and no split count set '
            f'({codes}) - they will not appear in the timetable until configured.'
        )

    # Sessions with no professor - still scheduled (room + time), just
    # exported with a blank staff field - warning only, not a blocker
    no_prof = session_base().filter(
        ~exists().where(ClassSessionProfessor.session_id == ClassSession.id)
    ).all()
    if no_prof:
        warnings.append(
            f'{len(no_prof)} session(s) have no professor assigned - still scheduled, but the '
            f'timetable and Template 2 export will show a blank staff field until one is assigned.'
        )

    qualification_mismatches = []
    for session in session_base().all():
        if not session.required_qualification:
            continue
        required = session.required_qualification.strip().lower()
        for professor in session.all_professors:
            tags = professor.qualification_tags
            if tags and required not in tags:
                qualification_mismatches.append(
                    f'{session.course.module_code} ({session.session_type}) requires '
                    f'"{session.required_qualification}", but {professor.user.name} '
                    'does not have that qualification tag.'
                )
    blockers.extend(qualification_mismatches)

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

    # AcademicCalendar and TimetableEntry use the SIT 13-week trimester model.
    # Reject malformed or out-of-range source data before solving so occurrences
    # are never silently discarded or written without a navigable calendar row.
    invalid_week_sessions = []
    for session in session_base().filter(ClassSession.teaching_weeks.isnot(None)).all():
        raw_parts = [part.strip() for part in session.teaching_weeks.split(',') if part.strip()]
        try:
            week_numbers = [int(part) for part in raw_parts]
        except ValueError:
            week_numbers = []
            invalid = True
        else:
            invalid = not week_numbers or any(week < 1 or week > 13 for week in week_numbers)
        if invalid:
            invalid_week_sessions.append(session)
    if invalid_week_sessions:
        examples = ', '.join(
            f'{session.course.module_code} ({session.teaching_weeks or "blank"})'
            for session in invalid_week_sessions[:5]
        )
        suffix = '...' if len(invalid_week_sessions) > 5 else ''
        blockers.append(
            f'{len(invalid_week_sessions)} session(s) have malformed or out-of-range '
            f'teaching weeks; only Weeks 1-13 are supported: {examples}{suffix}'
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

    blockers.extend(get_fixed_hard_constraint_conflicts(
        trimester_num=trimester_num,
        programme_ids=programme_ids,
    ))

    return blockers, warnings
