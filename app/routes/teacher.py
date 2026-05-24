from collections import defaultdict

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app import db
from app.models.class_session import ClassSession
from app.models.timetable_entry import TimetableEntry
from app.models.timeslot import TimeSlot
from app.models.availability_declaration import AvailabilityDeclaration
from app.models.timetable_flag import TimetableFlag
from app.models.flag_response import FlagResponse

teacher_bp = Blueprint('teacher', __name__, url_prefix='/teacher')

DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@teacher_bp.route('/dashboard')
@login_required
def dashboard():
    prof = current_user.professor_profile

    total_sessions = ClassSession.query.filter_by(professor_id=prof.id).count()

    # Count distinct sessions that have at least one published entry
    published_sessions = (
        db.session.query(TimetableEntry.class_session_id)
        .join(TimetableEntry.class_session)
        .filter(
            ClassSession.professor_id == prof.id,
            TimetableEntry.is_published == True,
        )
        .distinct()
        .count()
    )

    pending_declarations = AvailabilityDeclaration.query.filter_by(
        professor_id=prof.id, status='pending'
    ).count()

    open_flags = TimetableFlag.query.filter_by(
        professor_id=prof.id
    ).filter(TimetableFlag.status.in_(['open', 'acknowledged'])).count()

    # Most recent published trimester with entries for this professor
    latest_entry = (
        TimetableEntry.query
        .join(TimetableEntry.class_session)
        .filter(
            ClassSession.professor_id == prof.id,
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
        latest_trimester=latest_trimester,
    )


# ---------------------------------------------------------------------------
# My Timetable
# ---------------------------------------------------------------------------

@teacher_bp.route('/timetable')
@login_required
def timetable():
    prof = current_user.professor_profile

    # All trimesters that have published entries for this professor
    trimesters = [
        r[0] for r in (
            db.session.query(TimetableEntry.trimester)
            .join(TimetableEntry.class_session)
            .filter(
                ClassSession.professor_id == prof.id,
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
        .join(TimetableEntry.class_session)
        .join(TimetableEntry.timeslot)
        .filter(
            ClassSession.professor_id == prof.id,
            TimetableEntry.trimester == active_trimester,
            TimetableEntry.is_published == True,
        )
        .all()
    )

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
                            .join(TimetableEntry.class_session)
                            .join(TimetableEntry.timeslot)
                            .filter(
                                ClassSession.professor_id == prof.id,
                                TimetableEntry.trimester == active_trimester,
                                TimetableEntry.week_number == week_number,
                                TimetableEntry.is_published == True,
                            )
                            .all())

        week_grid = {day: {ts.period_label: None for ts in period_slots} for day in DAYS_ALL}
        for entry in week_entries_raw:
            d = entry.timeslot.day_of_week
            p = entry.timeslot.period_label
            if d in week_grid and p in week_grid[d]:
                week_grid[d][p] = entry

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
        days_all=DAYS_ALL,
    )


# ---------------------------------------------------------------------------
# Availability Declarations
# ---------------------------------------------------------------------------

@teacher_bp.route('/availability', methods=['GET', 'POST'])
@login_required
def availability():
    prof = current_user.professor_profile

    if request.method == 'POST':
        timeslot_id_raw = request.form.get('timeslot_id', '').strip()
        reason          = request.form.get('reason', '').strip()

        errors = []
        if not timeslot_id_raw:
            errors.append('Please select a timeslot.')
        if not reason:
            errors.append('Please provide a reason.')

        if not errors:
            ts_id = int(timeslot_id_raw)
            existing = AvailabilityDeclaration.query.filter_by(
                professor_id=prof.id,
                timeslot_id=ts_id,
            ).first()
            if existing:
                flash('You have already declared unavailability for this timeslot.', 'warning')
            else:
                db.session.add(AvailabilityDeclaration(
                    professor_id=prof.id,
                    timeslot_id=ts_id,
                    reason=reason,
                    status='pending',
                ))
                db.session.commit()
                flash('Declaration submitted. The admin will review and classify it.', 'success')
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

    open_flags = (TimetableFlag.query
                  .filter_by(professor_id=prof.id)
                  .filter(TimetableFlag.status.in_(['open', 'acknowledged']))
                  .order_by(TimetableFlag.created_at.desc())
                  .all())

    resolved_flags = (TimetableFlag.query
                      .filter_by(professor_id=prof.id, status='resolved')
                      .order_by(TimetableFlag.resolved_at.desc())
                      .all())

    return render_template('teacher/flags.html',
                           prof=prof,
                           open_flags=open_flags,
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

    if flag.status == 'resolved':
        flash('This flag has already been resolved.', 'info')
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
        flash('Response recorded. Flag marked as resolved. Thank you.', 'success')
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
                'Response recorded. Admin notification email could not be sent '
                f'(SMTP error: {error}). The admin will still see your response '
                'on the Flags page.',
                'warning'
            )

    return redirect(url_for('teacher.flags'))


@teacher_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_pw  = request.form.get('current_password', '').strip()
        new_pw      = request.form.get('new_password', '').strip()
        confirm_pw  = request.form.get('confirm_password', '').strip()

        if not current_user.check_password(current_pw):
            flash('Current password is incorrect.', 'danger')
        elif len(new_pw) < 8:
            flash('New password must be at least 8 characters.', 'danger')
        elif new_pw != confirm_pw:
            flash('New passwords do not match.', 'danger')
        else:
            current_user.set_password(new_pw)
            db.session.commit()
            flash('Password changed successfully.', 'success')
            return redirect(url_for('teacher.dashboard'))

    return render_template('teacher/change_password.html')


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
