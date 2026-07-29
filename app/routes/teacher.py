from collections import defaultdict
import re

from flask import Blueprint, Response, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user

from app import db
from app.models.class_session import ClassSession
from app.models.class_session_professor import ClassSessionProfessor
from app.models.timetable_entry import TimetableEntry
from app.models.timeslot import TimeSlot
from app.models.availability_declaration import AvailabilityDeclaration
from app.models.timetable_flag import TimetableFlag
from app.models.flag_response import FlagResponse
from app.utils.timetable import overlapping_entry_ids, select_preferred_layer

teacher_bp = Blueprint('teacher', __name__, url_prefix='/teacher')

DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']


def _teacher_timetable_uses_backbone(trimester, session_ids):
    """Choose one published timetable layer for a professor.

    Imported backbone rows and generated rows are alternative schedules. A
    teacher can be assigned to sessions in both, but displaying both layers at
    once creates duplicate classes and false professor clashes. Prefer the
    published generated layer when it exists for this teacher and trimester;
    otherwise retain the published backbone schedule as a fallback.
    """
    has_generated = (
        db.session.query(TimetableEntry.id)
        .filter(
            TimetableEntry.class_session_id.in_(session_ids),
            TimetableEntry.trimester == trimester,
            TimetableEntry.is_published.is_(True),
            TimetableEntry.is_backbone.is_(False),
        )
        .first()
    )
    return has_generated is None


@teacher_bp.before_request
@login_required
def require_professor():
    """Reject any non-professor user trying to access teacher routes."""
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    if current_user.role != 'professor':
        abort(403)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@teacher_bp.route('/dashboard')
@login_required
def dashboard():
    prof = current_user.professor_profile

    # Sessions this professor is assigned to (primary or co-teacher)
    my_session_ids = db.select(ClassSessionProfessor.session_id).where(
        ClassSessionProfessor.professor_id == prof.id
    )

    total_sessions = ClassSession.query.filter(ClassSession.id.in_(my_session_ids)).count()

    # Count distinct sessions that have at least one published entry
    published_sessions = (
        db.session.query(TimetableEntry.class_session_id)
        .filter(
            TimetableEntry.class_session_id.in_(my_session_ids),
            TimetableEntry.is_published == True,
        )
        .distinct()
        .count()
    )

    pending_declarations = AvailabilityDeclaration.query.filter_by(
        professor_id=prof.id, status='pending'
    ).count()

    open_flags = TimetableFlag.query.filter_by(
        professor_id=prof.id, status='open', notification_sent=True
    ).count()
    awaiting_admin_flags = TimetableFlag.query.filter_by(
        professor_id=prof.id, status='acknowledged'
    ).count()

    # Most recent published trimester with entries for this professor
    latest_entry = (
        TimetableEntry.query
        .filter(
            TimetableEntry.class_session_id.in_(my_session_ids),
            TimetableEntry.is_published == True,
        )
        .order_by(TimetableEntry.trimester.desc())
        .first()
    )
    latest_trimester = latest_entry.trimester if latest_entry else None

    return render_template(
        'teacher/dashboard.html',
        prof=prof,
        total_sessions=total_sessions,
        published_sessions=published_sessions,
        pending_declarations=pending_declarations,
        open_flags=open_flags,
        awaiting_admin_flags=awaiting_admin_flags,
        latest_trimester=latest_trimester,
    )


# ---------------------------------------------------------------------------
# My Timetable
# ---------------------------------------------------------------------------

@teacher_bp.route('/timetable')
@login_required
def timetable():
    prof = current_user.professor_profile

    # Sessions this professor is assigned to (primary or co-teacher)
    my_session_ids = db.select(ClassSessionProfessor.session_id).where(
        ClassSessionProfessor.professor_id == prof.id
    )

    # All trimesters that have published entries for this professor
    trimesters = [
        r[0] for r in (
            db.session.query(TimetableEntry.trimester)
            .filter(
                TimetableEntry.class_session_id.in_(my_session_ids),
                TimetableEntry.is_published == True,
            )
            .distinct()
            .order_by(TimetableEntry.trimester)
            .all()
        )
    ]

    active_trimester = request.args.get('trimester', trimesters[-1] if trimesters else '')
    # Fetch all published entries for this prof in the active trimester
    all_entries = (
        TimetableEntry.query
        .join(TimetableEntry.timeslot)
        .filter(
            TimetableEntry.class_session_id.in_(my_session_ids),
            TimetableEntry.trimester == active_trimester,
            TimetableEntry.is_published == True,
        )
        .all()
    )
    all_entries = select_preferred_layer(all_entries)

    # Deduplicate: one row per class_session (recurring weekly slot)
    seen = set()
    unique_entries = []
    for e in all_entries:
        if e.class_session_id not in seen:
            seen.add(e.class_session_id)
            unique_entries.append(e)

    unique_entries.sort(key=lambda e: (
        DAY_ORDER.index(e.timeslot.day_of_week),
        e.timeslot.start_time,
    ))

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
    conflicting_entry_ids = set()

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

        week_entries_raw = (TimetableEntry.query
                            .join(TimetableEntry.timeslot)
                            .filter(
                                TimetableEntry.class_session_id.in_(my_session_ids),
                                TimetableEntry.trimester == active_trimester,
                                TimetableEntry.week_number == week_number,
                                TimetableEntry.is_published == True,
                            )
                            .all())

        week_entries_raw = select_preferred_layer(week_entries_raw)
        conflicting_entry_ids = overlapping_entry_ids(week_entries_raw)
        week_grid = {day: {ts.period_label: [] for ts in period_slots} for day in DAYS_ALL}
        seen_week_sessions = set()
        for entry in week_entries_raw:
            if entry.class_session_id in seen_week_sessions:
                continue
            seen_week_sessions.add(entry.class_session_id)
            d = entry.timeslot.day_of_week
            p = entry.timeslot.period_label
            if d in week_grid and p in week_grid[d]:
                week_grid[d][p].append(entry)

    return render_template(
        'teacher/timetable.html',
        prof=prof,
        entries=unique_entries,
        trimesters=trimesters,
        active_trimester=active_trimester,
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


@teacher_bp.route('/timetable.ics')
@login_required
def timetable_ical():
    from app.models.academic_calendar import AcademicCalendar
    from app.utils.ical import build_ical

    trimester_key = request.args.get('trimester', '').strip()
    professor = current_user.professor_profile
    session_ids = db.select(ClassSessionProfessor.session_id).where(
        ClassSessionProfessor.professor_id == professor.id
    )
    entries = TimetableEntry.query.filter(
        TimetableEntry.class_session_id.in_(session_ids),
        TimetableEntry.trimester == trimester_key,
        TimetableEntry.is_published.is_(True),
    ).all()
    entries = select_preferred_layer(entries)
    if not entries:
        abort(404)
    calendar_weeks = AcademicCalendar.query.filter_by(trimester=trimester_key).all()
    response = Response(
        build_ical(entries, calendar_weeks, f'Teaching timetable - {trimester_key}'),
        mimetype='text/calendar; charset=utf-8',
    )
    response.headers['Content-Disposition'] = f'attachment; filename="teaching-{trimester_key}.ics"'
    return response


# ---------------------------------------------------------------------------
# Availability Declarations
# ---------------------------------------------------------------------------

@teacher_bp.route('/availability', methods=['GET', 'POST'])
@login_required
def availability():
    prof = current_user.professor_profile

    if request.method == 'POST':
        timeslot_id_raw = request.form.get('timeslot_id', '').strip()
        academic_year   = request.form.get('academic_year', '').strip().upper() or None
        trimester_raw   = request.form.get('trimester', '').strip()
        reason          = request.form.get('reason', '').strip()

        errors = []
        ts_id = None
        if not timeslot_id_raw:
            errors.append('Please select a timeslot.')
        else:
            try:
                ts_id = int(timeslot_id_raw)
            except ValueError:
                errors.append('That timeslot was not a valid selection - please choose from the dropdown.')
            else:
                if TimeSlot.query.get(ts_id) is None:
                    errors.append('That timeslot no longer exists - please refresh and try again.')
        if not reason:
            errors.append('Please provide a reason.')
        elif len(reason) > 255:
            reason = reason[:255]

        trimester = None
        if trimester_raw:
            try:
                trimester = int(trimester_raw)
            except ValueError:
                errors.append('Trimester must be 1, 2, or 3.')
            else:
                if trimester not in (1, 2, 3):
                    errors.append('Trimester must be 1, 2, or 3.')
        if academic_year and not re.fullmatch(r'AY\d{4}', academic_year):
            errors.append('Academic year must use the format AY2526.')
        if trimester is not None and academic_year is None:
            errors.append('Choose an academic year when limiting a declaration to one trimester.')

        if not errors:
            existing = AvailabilityDeclaration.query.filter_by(
                professor_id=prof.id,
                timeslot_id=ts_id,
                academic_year=academic_year,
                trimester=trimester,
            ).first()
            if existing:
                flash('You have already declared unavailability for this timeslot.', 'warning')
            else:
                db.session.add(AvailabilityDeclaration(
                    professor_id=prof.id,
                    timeslot_id=ts_id,
                    academic_year=academic_year,
                    trimester=trimester,
                    reason=reason,
                    status='pending',
                ))
                db.session.commit()
                flash('Availability request sent. The admin will review it before generation.', 'success')
                return redirect(url_for('teacher.availability'))
        else:
            for e in errors:
                flash(e, 'danger')

    # Load all timeslots grouped by day for the form
    all_slots = TimeSlot.query.all()
    all_slots.sort(key=lambda ts: (DAY_ORDER.index(ts.day_of_week), ts.start_time))

    slots_by_day = defaultdict(list)
    for ts in all_slots:
        slots_by_day[ts.day_of_week].append(ts)

    # Existing declarations (newest first)
    declarations = (
        AvailabilityDeclaration.query
        .filter_by(professor_id=prof.id)
        .order_by(AvailabilityDeclaration.submitted_at.desc())
        .all()
    )

    return render_template(
        'teacher/availability.html',
        prof=prof,
        slots_by_day=slots_by_day,
        day_order=DAY_ORDER,
        declarations=declarations,
    )


@teacher_bp.route('/flags')
@login_required
def flags():
    prof = current_user.professor_profile

    action_flags = (TimetableFlag.query
                    .filter_by(
                        professor_id=prof.id,
                        status='open',
                        notification_sent=True,
                    )
                    .order_by(TimetableFlag.created_at.desc())
                    .all())

    waiting_flags = (TimetableFlag.query
                     .filter_by(professor_id=prof.id, status='acknowledged')
                     .order_by(TimetableFlag.created_at.desc())
                     .all())

    resolved_flags = (TimetableFlag.query
                      .filter_by(professor_id=prof.id, status='resolved')
                      .order_by(TimetableFlag.resolved_at.desc())
                      .all())

    return render_template('teacher/flags.html',
                           prof=prof,
                           action_flags=action_flags,
                           waiting_flags=waiting_flags,
                           resolved_flags=resolved_flags)


@teacher_bp.route('/flags/<int:flag_id>/respond', methods=['POST'])
@login_required
def flag_respond(flag_id):
    from datetime import datetime
    prof     = current_user.professor_profile
    flag     = TimetableFlag.query.get_or_404(flag_id)

    if flag.professor_id != prof.id:
        flash('You can only respond to your own flags.', 'danger')
        return redirect(url_for('teacher.flags'))

    if flag.status != 'open':
        flash('This schedule request has already been answered.', 'info')
        return redirect(url_for('teacher.flags'))
    if not flag.notification_sent:
        flash('This schedule request has not been sent to you yet.', 'warning')
        return redirect(url_for('teacher.flags'))
    if FlagResponse.query.filter_by(
        flag_id=flag.id, professor_id=prof.id
    ).first() is not None:
        flash('You have already responded to this flag.', 'info')
        return redirect(url_for('teacher.flags'))

    response = request.form.get('response', '').strip()
    comments = request.form.get('comments', '').strip()

    if response not in ('can_proceed', 'cannot_proceed'):
        flash('Invalid response.', 'danger')
        return redirect(url_for('teacher.flags'))

    db.session.add(FlagResponse(
        flag_id      = flag.id,
        professor_id = prof.id,
        response     = response,
        comments     = comments or None,
    ))

    if response == 'can_proceed':
        flag.status      = 'resolved'
        flag.resolved_at = datetime.utcnow()
        db.session.commit()
        flash('Response recorded. The assigned slot is confirmed.', 'success')
    else:
        flag.status = 'acknowledged'
        db.session.commit()

        # Notify admin by email
        from app.utils.email import send_cannot_proceed_notification
        latest_response = FlagResponse.query.filter_by(flag_id=flag.id).order_by(
            FlagResponse.responded_at.desc()
        ).first()
        success, error = send_cannot_proceed_notification(flag, latest_response)

        if success:
            flash(
                'Response recorded. The admin has been notified by email and will '
                'arrange a substitute or adjust the timetable.',
                'info'
            )
        else:
            flash(
                'Response recorded. Admin notification email could not be sent. '
                'The admin will still see your response '
                'on the Schedule Responses page.',
                'warning'
            )

    return redirect(url_for('teacher.flags'))


@teacher_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Keep old bookmarks on the single shared account-security flow.

    The former professor-only page enforced a weaker password policy than
    ``/account/change-password``. It no longer accepts credentials itself.
    """
    return redirect(
        url_for('auth.change_password'),
        code=303 if request.method == 'POST' else 302,
    )


@teacher_bp.route('/availability/<int:decl_id>/delete', methods=['POST'])
@login_required
def availability_delete(decl_id):
    decl = AvailabilityDeclaration.query.get_or_404(decl_id)
    prof = current_user.professor_profile

    if decl.professor_id != prof.id:
        flash('You can only delete your own declarations.', 'danger')
        return redirect(url_for('teacher.availability'))

    if decl.status == 'classified':
        flash('This declaration has already been classified by the admin and cannot be deleted.', 'warning')
        return redirect(url_for('teacher.availability'))

    db.session.delete(decl)
    db.session.commit()
    flash('Declaration removed.', 'info')
    return redirect(url_for('teacher.availability'))
