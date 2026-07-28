from flask import Blueprint, render_template, request, abort
from flask_login import login_required, current_user

from app import db
from app.models.class_session import ClassSession
from app.models.timetable_entry import TimetableEntry
from app.models.timeslot import TimeSlot
from app.models.student_group import StudentGroup
from app.utils.timetable import (
    overlapping_entry_ids,
    select_preferred_layer,
    select_student_sections,
)

student_bp = Blueprint('student', __name__, url_prefix='/student')

DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']


def _student_timetable_uses_backbone(trimester, group_ids):
    """Choose exactly one published timetable layer for a student's cohort.

    Backbone rows are an imported reference schedule. Generated rows are the
    active solver output. Mixing both layers duplicates the same class (often
    with two different rooms) and can manufacture apparent student clashes.
    Prefer published generated rows whenever this cohort has them; otherwise
    fall back to its published backbone rows.
    """
    has_generated = (
        db.session.query(TimetableEntry.id)
        .join(TimetableEntry.class_session)
        .filter(
            TimetableEntry.trimester == trimester,
            TimetableEntry.is_published.is_(True),
            TimetableEntry.is_backbone.is_(False),
            ClassSession.student_group_id.in_(group_ids),
        )
        .first()
    )
    return has_generated is None


def _student_visible_group_ids(student_group):
    if student_group is None:
        return set()
    group_ids = {student_group.id}
    if student_group.parent_id:
        group_ids.add(student_group.parent_id)
    return group_ids


@student_bp.before_request
@login_required
def require_student():
    """Reject non-student accounts trying to access student routes."""
    if current_user.role != 'student':
        abort(403)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@student_bp.route('/dashboard')
@login_required
def dashboard():
    group_ids = _student_visible_group_ids(current_user.student_group)
    query = (db.session.query(TimetableEntry.trimester)
             .join(TimetableEntry.class_session)
             .filter(TimetableEntry.is_published.is_(True)))
    if group_ids:
        query = query.filter(ClassSession.student_group_id.in_(group_ids))
    else:
        query = query.filter(ClassSession.id == -1)
    published_trimesters = [
        row[0] for row in query.distinct().order_by(TimetableEntry.trimester).all()
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
    selected_group = current_user.student_group
    group_ids = _student_visible_group_ids(selected_group)
    trimester_query = (db.session.query(TimetableEntry.trimester)
                       .join(TimetableEntry.class_session)
                       .filter(TimetableEntry.is_published.is_(True)))
    if group_ids:
        trimester_query = trimester_query.filter(
            ClassSession.student_group_id.in_(group_ids)
        )
    else:
        trimester_query = trimester_query.filter(ClassSession.id == -1)
    trimesters = [
        row[0] for row in trimester_query.distinct().order_by(TimetableEntry.trimester).all()
    ]

    active_trimester = request.args.get('trimester', trimesters[-1] if trimesters else '')

    # Auto-load the student's assigned group - no manual selection needed.
    # student_group_id is set by the admin on the student's account.
    entries = []
    conflicting_entry_ids = set()

    if selected_group and active_trimester:
        # Show sessions for the student's sub-group AND the parent group
        # (parent group covers shared sessions not split by sub-group).
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

        all_entries = select_preferred_layer(all_entries)
        all_entries = select_student_sections(all_entries, current_user.id)

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

    # ---------------------------------------------------------------------------
    # Weekly view support
    # ---------------------------------------------------------------------------
    from app.models.academic_calendar import AcademicCalendar

    DAYS_ALL = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    view_mode        = request.args.get('view', 'list')
    week_number      = request.args.get('week', 1, type=int)
    calendar_weeks   = []
    period_slots     = []
    week_grid        = {d: {} for d in DAYS_ALL}
    current_cal_week = None
    prev_week_num    = None
    next_week_num    = None

    if active_trimester:
        calendar_weeks = (AcademicCalendar.query
                          .filter_by(trimester=active_trimester)
                          .order_by(AcademicCalendar.week_number)
                          .all())

        all_week_nums = [cw.week_number for cw in calendar_weeks] if calendar_weeks else list(range(1, 14))
        if week_number not in all_week_nums:
            week_number = all_week_nums[0]

        current_idx      = all_week_nums.index(week_number)
        prev_week_num    = all_week_nums[current_idx - 1] if current_idx > 0 else None
        next_week_num    = all_week_nums[current_idx + 1] if current_idx < len(all_week_nums) - 1 else None
        current_cal_week = next((cw for cw in calendar_weeks if cw.week_number == week_number), None)

        period_slots = (TimeSlot.query
                        .filter_by(day_of_week='Monday')
                        .order_by(TimeSlot.start_time, TimeSlot.end_time, TimeSlot.period_label)
                        .all())

        if selected_group:
            week_entries_raw = (TimetableEntry.query
                                .join(TimetableEntry.class_session)
                                .join(TimetableEntry.timeslot)
                                .filter(
                                    TimetableEntry.trimester == active_trimester,
                                    TimetableEntry.week_number == week_number,
                                    TimetableEntry.is_published == True,
                                    ClassSession.student_group_id.in_(group_ids),
                                )
                                .all())

            week_entries_raw = select_preferred_layer(week_entries_raw)
            week_entries_raw = select_student_sections(
                week_entries_raw, current_user.id
            )
            conflicting_entry_ids = overlapping_entry_ids(week_entries_raw)
            week_grid = {day: {ts.period_label: [] for ts in period_slots} for day in DAYS_ALL}
            seen_week_sessions = set()
            for entry in week_entries_raw:
                # Defensive guard against duplicate rows for one session/week
                # within a single imported layer.
                if entry.class_session_id in seen_week_sessions:
                    continue
                seen_week_sessions.add(entry.class_session_id)
                d = entry.timeslot.day_of_week
                p = entry.timeslot.period_label
                if d in week_grid and p in week_grid[d]:
                    week_grid[d][p].append(entry)

    return render_template(
        'student/timetable.html',
        trimesters=trimesters,
        active_trimester=active_trimester,
        selected_group=selected_group,
        entries=entries,
        view_mode=view_mode,
        week_number=week_number,
        calendar_weeks=calendar_weeks,
        current_cal_week=current_cal_week,
        prev_week_num=prev_week_num,
        next_week_num=next_week_num,
        period_slots=period_slots,
        week_grid=week_grid,
        conflicting_entry_ids=conflicting_entry_ids,
        days_all=DAYS_ALL,
    )
