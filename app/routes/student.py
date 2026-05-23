from flask import Blueprint, render_template, request
from flask_login import login_required, current_user

from app import db
from app.models.class_session import ClassSession
from app.models.timetable_entry import TimetableEntry
from app.models.timeslot import TimeSlot
from app.models.student_group import StudentGroup

student_bp = Blueprint('student', __name__, url_prefix='/student')

DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@student_bp.route('/dashboard')
@login_required
def dashboard():
    # Count published trimesters visible to students
    published_trimesters = [
        r[0] for r in (
            db.session.query(TimetableEntry.trimester)
            .filter_by(is_published=True)
            .distinct()
            .order_by(TimetableEntry.trimester)
            .all()
        )
    ]
    latest_trimester = published_trimesters[-1] if published_trimesters else None

    return render_template(
        'student/dashboard.html',
        published_trimesters=published_trimesters,
        latest_trimester=latest_trimester,
    )


# ---------------------------------------------------------------------------
# My Timetable
# ---------------------------------------------------------------------------

@student_bp.route('/timetable')
@login_required
def timetable():
    # All trimesters with published entries
    trimesters = [
        r[0] for r in (
            db.session.query(TimetableEntry.trimester)
            .filter_by(is_published=True)
            .distinct()
            .order_by(TimetableEntry.trimester)
            .all()
        )
    ]

    active_trimester = request.args.get('trimester', trimesters[-1] if trimesters else '')
    group_id_raw     = request.args.get('group_id', '')

    # All groups for the dropdown (top-level + sub-groups, sorted by year then label)
    all_groups = (
        StudentGroup.query
        .order_by(StudentGroup.year_level, StudentGroup.group_label)
        .all()
    )

    selected_group = None
    entries = []

    if active_trimester and group_id_raw:
        try:
            selected_group = StudentGroup.query.get(int(group_id_raw))
        except (ValueError, TypeError):
            selected_group = None

    if selected_group:
        # Resolve which group IDs this student belongs to:
        # If they picked a sub-group, include both the sub-group and its parent.
        # If they picked a top-level group, include the top-level only (no sub-group entries).
        group_ids = {selected_group.id}
        if selected_group.parent_id:
            group_ids.add(selected_group.parent_id)

        all_entries = (
            TimetableEntry.query
            .join(TimetableEntry.class_session)
            .join(TimetableEntry.timeslot)
            .filter(
                TimetableEntry.trimester == active_trimester,
                TimetableEntry.is_published == True,
                ClassSession.student_group_id.in_(group_ids),
            )
            .all()
        )

        # Deduplicate: one row per class_session (recurring weekly slot)
        seen = set()
        unique = []
        for e in all_entries:
            if e.class_session_id not in seen:
                seen.add(e.class_session_id)
                unique.append(e)

        unique.sort(key=lambda e: (
            DAY_ORDER.index(e.timeslot.day_of_week),
            e.timeslot.start_time,
        ))
        entries = unique

    return render_template(
        'student/timetable.html',
        trimesters=trimesters,
        active_trimester=active_trimester,
        all_groups=all_groups,
        selected_group=selected_group,
        group_id=group_id_raw,
        entries=entries,
    )
