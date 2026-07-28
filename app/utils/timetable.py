"""Shared timetable projection helpers for student and teacher views."""

from collections import defaultdict

from app.engine.solver import _timeslots_overlap
from app.engine.solver import _parallel_section_alternatives


def select_preferred_layer(entries):
    """Choose generated rows per session, with backbone as a safe fallback.

    Programme-batch generation may intentionally omit a complete programme.
    Selecting one layer globally for an entire student group or professor can
    therefore hide unrelated backbone sessions. Selection per class session
    preserves the fallback without ever mixing duplicate rows for that class.
    """
    by_session = defaultdict(list)
    for entry in entries:
        by_session[entry.class_session_id].append(entry)

    selected = []
    for session_entries in by_session.values():
        generated = [entry for entry in session_entries if not entry.is_backbone]
        selected.extend(generated if generated else session_entries)
    return selected


def select_student_sections(entries, selector_key):
    """Choose one deterministic section from each parallel alternative set.

    The source workbook has cohort-level membership but no per-student section
    enrolment.  A stable account-based choice prevents a student from seeing
    every T1/T2/P1/P2 alternative, while retaining sequential same-labelled
    rows whose teaching weeks do not overlap.
    """
    entries = list(entries)
    representative = {}
    for entry in entries:
        representative.setdefault(entry.class_session_id, entry.class_session)

    sessions = list(representative.values())
    parent = {session.id: session.id for session in sessions}

    def find(session_id):
        while parent[session_id] != session_id:
            parent[session_id] = parent[parent[session_id]]
            session_id = parent[session_id]
        return session_id

    def union(left_id, right_id):
        left_root, right_root = find(left_id), find(right_id)
        if left_root != right_root:
            parent[left_root] = right_root

    for index, left in enumerate(sessions):
        for right in sessions[index + 1:]:
            if _parallel_section_alternatives(left, right):
                union(left.id, right.id)

    components = defaultdict(list)
    for session in sessions:
        components[find(session.id)].append(session)

    allowed_session_ids = set()
    stable_key = int(selector_key or 0)
    for component in components.values():
        ordered = sorted(component, key=lambda session: (
            session.group_label or '', session.id
        ))
        if len(ordered) == 1:
            allowed_session_ids.add(ordered[0].id)
        else:
            allowed_session_ids.add(ordered[stable_key % len(ordered)].id)

    return [
        entry for entry in entries
        if entry.class_session_id in allowed_session_ids
    ]


def apply_explicit_student_sections(entries, user_id, academic_year, trimester):
    """Apply recorded section assignments, falling back only where unassigned."""
    from app.models.student_enrollment import StudentSectionAssignment
    assignments = StudentSectionAssignment.query.filter_by(
        user_id=user_id,
        academic_year=academic_year,
        trimester=trimester,
    ).all()
    if not assignments:
        return select_student_sections(entries, user_id)

    assigned_ids = {assignment.class_session_id for assignment in assignments}
    assigned_keys = {
        (assignment.class_session.course_id, assignment.class_session.session_type)
        for assignment in assignments
    }
    filtered = [
        entry for entry in entries
        if (
            (entry.class_session.course_id, entry.class_session.session_type)
            not in assigned_keys
            or entry.class_session_id in assigned_ids
        )
    ]
    return select_student_sections(filtered, user_id)


def overlapping_entry_ids(entries):
    """Ids of entries that overlap another visible class in real clock time."""
    conflicts = set()
    entries = list(entries)
    for index, left in enumerate(entries):
        for right in entries[index + 1:]:
            if left.timeslot is None or right.timeslot is None:
                continue
            if not _timeslots_overlap(left.timeslot, right.timeslot):
                continue
            left_shared = left.class_session.shared_module_group_id
            right_shared = right.class_session.shared_module_group_id
            if left_shared is not None and left_shared == right_shared:
                continue
            conflicts.update((left.id, right.id))
    return conflicts
