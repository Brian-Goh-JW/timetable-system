import math
import threading
import uuid
import time as _time
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_login import login_required, current_user
from app import db
from app.models.course import Course
from app.models.professor import Professor
from app.models.room import Room
from app.models.timetable_flag import TimetableFlag
from app.models.availability_declaration import AvailabilityDeclaration
from app.models.user import User
from app.models.student_group import StudentGroup
from app.models.programme import Programme
from app.models.class_session import ClassSession
from app.models.class_session_professor import ClassSessionProfessor
from app.models.timetable_entry import TimetableEntry
from app.models.timeslot import TimeSlot
from app.models.audit_log import AuditLog

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# In-memory store for background solver tasks: {task_id: {status, ...}}
# Entries expire after 2 hours (cleaned up lazily on new task start).
_solver_tasks: dict = {}
_TASK_TTL = 7200  # seconds

# Official SIT academic calendar — week 1 start dates (all Mondays).
# Source: https://www.singaporetech.edu.sg/admissions/undergraduate/academic-calendar-sit-and-joint-programmes
SIT_ACADEMIC_CALENDAR = {
    'AY2425': {1: '2024-09-02', 2: '2025-01-06', 3: '2025-05-05'},
    'AY2526': {1: '2025-09-01', 2: '2026-01-05', 3: '2026-05-04'},
    'AY2627': {1: '2026-08-31', 2: '2027-01-04', 3: '2027-05-03'},
}


@admin_bp.before_request
@login_required
def require_admin():
    """Reject any non-admin user trying to access admin routes."""
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    if current_user.role != 'admin':
        abort(403)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@admin_bp.route('/dashboard')
@login_required
def dashboard():
    from sqlalchemy import exists as sa_exists
    # Only flag courses that have NO sessions yet AND no split_count (mirrors checker.py logic)
    _has_session = sa_exists().where(ClassSession.course_id == Course.id)
    courses_missing_split = Course.query.filter(
        Course.delivery_mode.in_(['f2f', 'hybrid']),
        Course.split_count.is_(None),
        ~_has_session,
    ).order_by(Course.year_level, Course.module_code).all()

    stats = {
        'total_courses':         Course.query.count(),
        'courses_missing_split': len(courses_missing_split),
        'total_professors':      Professor.query.count(),
        'total_rooms':           Room.query.filter_by(is_active=True).count(),
        'open_flags':            TimetableFlag.query.filter_by(status='open').count(),
        'pending_declarations':  AvailabilityDeclaration.query.filter_by(status='pending').count(),
    }

    # KPIs — computed from the most recently generated trimester
    kpis = None
    latest_row = db.session.query(TimetableEntry.trimester)\
        .order_by(TimetableEntry.trimester.desc()).first()
    if latest_row:
        tri = latest_row[0]
        sessions_scheduled = db.session.query(TimetableEntry.class_session_id)\
            .filter(TimetableEntry.trimester == tri).distinct().count()
        used_room_slots = db.session.query(
            TimetableEntry.room_id, TimetableEntry.timeslot_id
        ).filter(
            TimetableEntry.trimester == tri,
            TimetableEntry.room_id.isnot(None)
        ).distinct().count()
        total_active_rooms = Room.query.filter_by(is_active=True).count()
        total_timeslots = db.session.query(TimeSlot).count()
        room_util = round(used_room_slots / (total_active_rooms * total_timeslots) * 100, 1) \
            if total_active_rooms and total_timeslots else 0
        profs_covered = db.session.query(ClassSessionProfessor.professor_id)\
            .join(TimetableEntry, TimetableEntry.class_session_id == ClassSessionProfessor.session_id)\
            .filter(TimetableEntry.trimester == tri).distinct().count()
        kpis = {
            'trimester':           tri,
            'sessions_scheduled':  sessions_scheduled,
            'room_util_pct':       room_util,
            'profs_covered':       profs_covered,
            'hard_conflicts':      0,
        }

    return render_template(
        'admin/dashboard.html',
        stats=stats,
        courses_missing_split=courses_missing_split,
        kpis=kpis,
    )


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------

@admin_bp.route('/courses')
@login_required
def courses():
    all_courses = Course.query.order_by(Course.year_level, Course.module_code).all()
    return render_template('admin/courses.html', courses=all_courses)


@admin_bp.route('/courses/<int:course_id>/edit', methods=['GET', 'POST'])
@login_required
def course_edit(course_id):
    course = Course.query.get_or_404(course_id)

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        remarks = request.form.get('remarks', '').strip()
        split_count_raw = request.form.get('split_count', '').strip()

        # Validate
        if not title:
            flash('Module title cannot be empty.', 'danger')
            return render_template('admin/course_edit.html', course=course)

        split_count = None
        if course.delivery_mode in ('f2f', 'hybrid'):
            if split_count_raw == '':
                split_count = None      # Admin left it blank — still not set
            else:
                try:
                    split_count = int(split_count_raw)
                    if split_count < 1:
                        raise ValueError
                except ValueError:
                    flash('Split count must be a whole number of 1 or more.', 'danger')
                    return render_template('admin/course_edit.html', course=course)

        course.title = title
        course.remarks = remarks or None
        course.split_count = split_count
        db.session.commit()

        flash(f'{course.module_code} updated successfully.', 'success')
        return redirect(url_for('admin.courses'))

    return render_template('admin/course_edit.html', course=course)


# ---------------------------------------------------------------------------
# Professors
# ---------------------------------------------------------------------------

@admin_bp.route('/professors')
@login_required
def professors():
    all_professors = (Professor.query
                      .join(Professor.user)
                      .order_by(User.name)
                      .all())
    return render_template('admin/professors.html', professors=all_professors)


@admin_bp.route('/professors/add', methods=['GET', 'POST'])
@login_required
def professor_add():
    if request.method == 'POST':
        name       = request.form.get('name', '').strip()
        email      = request.form.get('email', '').strip().lower()
        staff_id   = request.form.get('staff_id', '').strip()
        department = request.form.get('department', '').strip()
        password   = request.form.get('password', '').strip()

        errors = []
        if not name:       errors.append('Name is required.')
        if not email:      errors.append('Email is required.')
        if not staff_id:   errors.append('Staff ID is required.')
        if not department: errors.append('Department is required.')
        if not password:   errors.append('Temporary password is required.')

        if User.query.filter_by(email=email).first():
            errors.append('An account with this email already exists.')
        if Professor.query.filter_by(staff_id=staff_id).first():
            errors.append('A professor with this Staff ID already exists.')

        if errors:
            departments = sorted({p.department for p in Professor.query.all() if p.department})
            for e in errors:
                flash(e, 'danger')
            return render_template('admin/professor_add.html', form=request.form, departments=departments)

        # Create User account + Professor profile in one transaction
        user = User(name=name, email=email, role='professor')
        user.set_password(password)
        db.session.add(user)
        db.session.flush()      # get user.id before committing

        professor = Professor(user_id=user.id, staff_id=staff_id, department=department)
        db.session.add(professor)
        db.session.commit()

        flash(f'Professor {name} added successfully.', 'success')
        return redirect(url_for('admin.professors'))

    departments = sorted({p.department for p in Professor.query.all() if p.department})
    return render_template('admin/professor_add.html', form={}, departments=departments)


@admin_bp.route('/professors/<int:professor_id>/edit', methods=['GET', 'POST'])
@login_required
def professor_edit(professor_id):
    professor = Professor.query.get_or_404(professor_id)
    user = professor.user

    if request.method == 'POST':
        name         = request.form.get('name', '').strip()
        email        = request.form.get('email', '').strip().lower()
        staff_id     = request.form.get('staff_id', '').strip()
        department   = request.form.get('department', '').strip()
        new_password = request.form.get('new_password', '').strip()

        errors = []
        if not name:       errors.append('Name is required.')
        if not email:      errors.append('Email is required.')
        if not staff_id:   errors.append('Staff ID is required.')
        if not department: errors.append('Department is required.')

        existing_email = User.query.filter_by(email=email).first()
        if existing_email and existing_email.id != user.id:
            errors.append('Another account is already using this email.')

        existing_sid = Professor.query.filter_by(staff_id=staff_id).first()
        if existing_sid and existing_sid.id != professor.id:
            errors.append('Another professor already has this Staff ID.')

        departments = sorted({p.department for p in Professor.query.all() if p.department})
        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('admin/professor_edit.html', professor=professor, departments=departments)

        user.name            = name
        user.email           = email
        professor.staff_id   = staff_id
        professor.department = department

        if new_password:
            user.set_password(new_password)
            flash('Password has been reset.', 'info')

        db.session.commit()
        flash(f'{name} updated successfully.', 'success')
        return redirect(url_for('admin.professors'))

    departments = sorted({p.department for p in Professor.query.all() if p.department})
    return render_template('admin/professor_edit.html', professor=professor, departments=departments)


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------

@admin_bp.route('/students')
@login_required
def students():
    all_students = (User.query
                    .filter_by(role='student')
                    .order_by(User.name)
                    .all())
    return render_template('admin/students.html', students=all_students)


@admin_bp.route('/students/add', methods=['GET', 'POST'])
@login_required
def student_add():
    all_groups = (StudentGroup.query
                  .order_by(StudentGroup.year_level, StudentGroup.group_label)
                  .all())

    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '').strip()
        group_id = request.form.get('student_group_id', '').strip()

        errors = []
        if not name:     errors.append('Full name is required.')
        if not email:    errors.append('Email address is required.')
        if not password: errors.append('Password is required.')

        if email and User.query.filter_by(email=email).first():
            errors.append(f'An account with email {email} already exists.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('admin/student_add.html',
                                   all_groups=all_groups, form=request.form)

        student = User(
            name             = name,
            email            = email,
            role             = 'student',
            student_group_id = int(group_id) if group_id else None,
        )
        student.set_password(password)
        db.session.add(student)
        db.session.commit()
        flash(f'Student account created for {name}.', 'success')
        return redirect(url_for('admin.students'))

    return render_template('admin/student_add.html', all_groups=all_groups, form={})


@admin_bp.route('/students/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def student_edit(user_id):
    student = User.query.get_or_404(user_id)
    if student.role != 'student':
        abort(404)

    all_groups = (StudentGroup.query
                  .order_by(StudentGroup.year_level, StudentGroup.group_label)
                  .all())

    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '').strip()
        group_id = request.form.get('student_group_id', '').strip()

        errors = []
        if not name:  errors.append('Full name is required.')
        if not email: errors.append('Email address is required.')

        existing = User.query.filter_by(email=email).first()
        if existing and existing.id != student.id:
            errors.append(f'Another account is already using {email}.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('admin/student_edit.html',
                                   student=student, all_groups=all_groups)

        student.name             = name
        student.email            = email
        student.student_group_id = int(group_id) if group_id else None
        if password:
            student.set_password(password)
            flash('Password has been reset.', 'info')

        db.session.commit()
        flash(f'{name} updated successfully.', 'success')
        return redirect(url_for('admin.students'))

    return render_template('admin/student_edit.html',
                           student=student, all_groups=all_groups)


@admin_bp.route('/students/<int:user_id>/delete', methods=['POST'])
@login_required
def student_delete(user_id):
    student = User.query.get_or_404(user_id)
    if student.role != 'student':
        abort(404)
    name = student.name
    db.session.delete(student)
    db.session.commit()
    flash(f'Student account for {name} deleted.', 'success')
    return redirect(url_for('admin.students'))


# ---------------------------------------------------------------------------
# Rooms
# ---------------------------------------------------------------------------

@admin_bp.route('/rooms')
@login_required
def rooms():
    building_filter = request.args.get('building', '')
    query = Room.query.order_by(Room.building, Room.room_code)
    if building_filter:
        query = query.filter_by(building=building_filter)
    all_rooms = query.all()

    buildings = [r[0] for r in db.session.query(Room.building).distinct().order_by(Room.building).all()]

    return render_template('admin/rooms.html',
                           rooms=all_rooms,
                           buildings=buildings,
                           active_building=building_filter)


@admin_bp.route('/rooms/add', methods=['GET', 'POST'])
@login_required
def room_add():
    if request.method == 'POST':
        room_code = request.form.get('room_code', '').strip().upper()
        building  = request.form.get('building', '').strip().upper()
        capacity  = request.form.get('capacity', '').strip()
        room_type = request.form.get('room_type', '').strip()

        errors = []
        if not room_code: errors.append('Room code is required.')
        if not building:  errors.append('Building is required.')
        if not room_type: errors.append('Room type is required.')

        try:
            capacity = int(capacity)
            if capacity < 1:
                raise ValueError
        except (ValueError, TypeError):
            errors.append('Capacity must be a whole number of 1 or more.')
            capacity = ''

        if Room.query.filter_by(room_code=room_code).first():
            errors.append(f'A room with code {room_code} already exists.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('admin/room_add.html', form=request.form)

        db.session.add(Room(
            room_code=room_code,
            building=building,
            capacity=capacity,
            room_type=room_type,
            is_active=True,
        ))
        db.session.commit()
        flash(f'Room {room_code} added successfully.', 'success')
        return redirect(url_for('admin.rooms'))

    return render_template('admin/room_add.html', form={})


@admin_bp.route('/rooms/<int:room_id>/edit', methods=['GET', 'POST'])
@login_required
def room_edit(room_id):
    room = Room.query.get_or_404(room_id)

    if request.method == 'POST':
        room_code = request.form.get('room_code', '').strip().upper()
        building  = request.form.get('building', '').strip().upper()
        capacity  = request.form.get('capacity', '').strip()
        room_type = request.form.get('room_type', '').strip()

        errors = []
        if not room_code: errors.append('Room code is required.')
        if not building:  errors.append('Building is required.')
        if not room_type: errors.append('Room type is required.')

        try:
            capacity = int(capacity)
            if capacity < 1:
                raise ValueError
        except (ValueError, TypeError):
            errors.append('Capacity must be a whole number of 1 or more.')
            capacity = ''

        existing = Room.query.filter_by(room_code=room_code).first()
        if existing and existing.id != room.id:
            errors.append(f'Another room with code {room_code} already exists.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('admin/room_edit.html', room=room)

        room.room_code = room_code
        room.building  = building
        room.capacity  = capacity
        room.room_type = room_type
        db.session.commit()

        flash(f'Room {room_code} updated successfully.', 'success')
        return redirect(url_for('admin.rooms'))

    return render_template('admin/room_edit.html', room=room)


@admin_bp.route('/rooms/<int:room_id>/toggle', methods=['POST'])
@login_required
def room_toggle(room_id):
    room = Room.query.get_or_404(room_id)
    room.is_active = not room.is_active
    db.session.commit()
    status = 'activated' if room.is_active else 'deactivated'
    flash(f'Room {room.room_code} {status}.', 'info')
    return redirect(url_for('admin.rooms', building=request.form.get('building', '')))


@admin_bp.route('/rooms/<int:room_id>/delete', methods=['POST'])
@login_required
def room_delete(room_id):
    room = Room.query.get_or_404(room_id)

    if room.timetable_entries:
        flash(f'Room {room.room_code} cannot be deleted — it has assigned timetable entries. Deactivate it instead.', 'danger')
        return redirect(url_for('admin.rooms'))

    code = room.room_code
    db.session.delete(room)
    db.session.commit()
    flash(f'Room {code} deleted.', 'success')
    return redirect(url_for('admin.rooms'))


# ---------------------------------------------------------------------------
# Student Groups
# ---------------------------------------------------------------------------

@admin_bp.route('/student-groups')
@login_required
def student_groups():
    # Only top-level groups (no parent); sub-groups shown nested under them
    top_level = (StudentGroup.query
                 .filter_by(parent_id=None)
                 .order_by(StudentGroup.year_level, StudentGroup.group_label)
                 .all())
    return render_template('admin/student_groups.html', groups=top_level)


@admin_bp.route('/student-groups/add', methods=['GET', 'POST'])
@login_required
def student_group_add():
    programmes = Programme.query.order_by(Programme.code).all()

    if request.method == 'POST':
        programme_id = request.form.get('programme_id', '').strip()
        year_level   = request.form.get('year_level', '').strip()
        intake_size  = request.form.get('intake_size', '').strip()

        errors = []
        if not programme_id: errors.append('Programme is required.')

        try:
            year_level = int(year_level)
            if year_level < 1:
                raise ValueError
        except (ValueError, TypeError):
            errors.append('Year level must be a positive number.')
            year_level = ''

        try:
            intake_size = int(intake_size)
            if intake_size < 1:
                raise ValueError
        except (ValueError, TypeError):
            errors.append('Intake size must be a positive number.')
            intake_size = ''

        programme = Programme.query.get(int(programme_id)) if programme_id else None
        group_label = f'{programme.code}-Y{year_level}' if programme and year_level != '' else ''

        if group_label and StudentGroup.query.filter_by(group_label=group_label).first():
            errors.append(f'A group with label {group_label} already exists.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('admin/student_group_add.html',
                                   programmes=programmes, form=request.form)

        group = StudentGroup(
            programme_id=int(programme_id),
            year_level=year_level,
            group_label=group_label,
            intake_size=intake_size,
            parent_id=None,
        )
        db.session.add(group)
        db.session.commit()
        flash(f'Student group {group_label} created successfully.', 'success')
        return redirect(url_for('admin.student_groups'))

    return render_template('admin/student_group_add.html',
                           programmes=programmes, form={})


@admin_bp.route('/student-groups/<int:group_id>/edit', methods=['GET', 'POST'])
@login_required
def student_group_edit(group_id):
    group = StudentGroup.query.get_or_404(group_id)
    programmes = Programme.query.order_by(Programme.code).all()

    if request.method == 'POST':
        intake_size = request.form.get('intake_size', '').strip()

        errors = []
        try:
            intake_size = int(intake_size)
            if intake_size < 1:
                raise ValueError
        except (ValueError, TypeError):
            errors.append('Intake size must be a positive number.')
            intake_size = ''

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('admin/student_group_edit.html', group=group)

        group.intake_size = intake_size
        db.session.commit()
        flash(f'Group {group.group_label} updated successfully.', 'success')
        return redirect(url_for('admin.student_groups'))

    return render_template('admin/student_group_edit.html', group=group)


@admin_bp.route('/student-groups/<int:group_id>/generate-subgroups', methods=['POST'])
@login_required
def student_group_generate(group_id):
    parent = StudentGroup.query.get_or_404(group_id)
    num_raw = request.form.get('num_subgroups', '').strip()

    try:
        num = int(num_raw)
        if num < 2:
            raise ValueError
    except (ValueError, TypeError):
        flash('Number of sub-groups must be 2 or more.', 'danger')
        return redirect(url_for('admin.student_groups'))

    # Guard: refuse if existing sub-groups already have sessions assigned (would cause FK violation)
    existing_subs = StudentGroup.query.filter_by(parent_id=parent.id).all()
    if existing_subs:
        sub_ids = [s.id for s in existing_subs]
        blocking = ClassSession.query.filter(
            ClassSession.student_group_id.in_(sub_ids)
        ).first()
        if blocking:
            flash(
                f'Cannot regenerate sub-groups for {parent.group_label} — '
                f'existing sub-groups have sessions assigned. '
                f'Clear all session assignments first.',
                'danger'
            )
            return redirect(url_for('admin.student_groups'))

    # Delete existing sub-groups for this parent before regenerating
    StudentGroup.query.filter_by(parent_id=parent.id).delete()

    sub_size = math.ceil(parent.intake_size / num)
    labels = [chr(65 + i) for i in range(num)]   # A, B, C, ...

    for label in labels:
        db.session.add(StudentGroup(
            programme_id=parent.programme_id,
            year_level=parent.year_level,
            group_label=f'{parent.group_label}-{label}',
            intake_size=sub_size,
            parent_id=parent.id,
        ))

    db.session.commit()
    flash(f'Generated {num} sub-groups for {parent.group_label}.', 'success')
    return redirect(url_for('admin.student_groups'))


@admin_bp.route('/student-groups/<int:group_id>/delete', methods=['POST'])
@login_required
def student_group_delete(group_id):
    group = StudentGroup.query.get_or_404(group_id)

    # Check this group AND all its sub-groups for assigned sessions
    all_ids = [group.id] + [sub.id for sub in group.sub_groups]
    blocking = ClassSession.query.filter(
        ClassSession.student_group_id.in_(all_ids)
    ).first()
    if blocking:
        flash(
            f'Group {group.group_label} cannot be deleted — '
            f'it or its sub-groups have sessions assigned. '
            f'Clear all session assignments first.',
            'danger'
        )
        return redirect(url_for('admin.student_groups'))

    label = group.group_label
    # Delete sub-groups first if this is a parent
    StudentGroup.query.filter_by(parent_id=group.id).delete()
    db.session.delete(group)
    db.session.commit()
    flash(f'Group {label} deleted.', 'success')
    return redirect(url_for('admin.student_groups'))


# ---------------------------------------------------------------------------
# Session Assignment
# ---------------------------------------------------------------------------

@admin_bp.route('/courses/<int:course_id>/sessions')
@login_required
def course_sessions(course_id):
    course = Course.query.get_or_404(course_id)

    # Sync f2f session count to split_count (expand up or trim down).
    from collections import defaultdict
    target_count = course.split_count if course.split_count and course.split_count > 0 else 1
    f2f_sessions = [s for s in course.class_sessions if s.delivery_mode == 'f2f']
    type_counts = defaultdict(list)
    for s in f2f_sessions:
        type_counts[s.session_type].append(s)

    changed = False
    for stype, sessions in type_counts.items():
        # Expand: create sessions if below target
        while len(sessions) < target_count:
            new_session = ClassSession(
                course_id=course.id,
                session_type=stype,
                delivery_mode='f2f',
                duration_hours=sessions[0].duration_hours,
                professor_id=None,
                student_group_id=None,
            )
            db.session.add(new_session)
            sessions.append(new_session)
            changed = True

        # Trim: remove excess sessions if above target.
        # Only removes sessions that are fully unassigned with no timetable entries.
        while len(sessions) > target_count:
            last = sessions[-1]
            if not last.professor_id and not last.student_group_id and not last.timetable_entries:
                db.session.delete(last)
                sessions.pop()
                changed = True
            else:
                break  # Leave assigned sessions alone — admin must clear them manually

    if changed:
        db.session.commit()

    # Reload sessions after potential expansion
    sessions = (ClassSession.query
                .filter_by(course_id=course_id)
                .order_by(ClassSession.delivery_mode, ClassSession.session_type)
                .all())

    professors = Professor.query.join(Professor.user).order_by(User.name).all()

    # Student groups: top-level for the course's year, plus their sub-groups
    top_groups = StudentGroup.query.filter_by(
        year_level=course.year_level, parent_id=None
    ).order_by(StudentGroup.group_label).all()

    # Build a flat list: top-level + sub-groups indented for the dropdown
    group_choices = []
    for g in top_groups:
        group_choices.append((g.id, g.group_label, False))
        for sub in sorted(g.sub_groups, key=lambda x: x.group_label):
            group_choices.append((sub.id, f'  {sub.group_label}', True))

    # Build compatible timeslots per session for the fixed-slot dropdown
    # Only show timeslots that match the session's type and duration
    all_timeslots = (TimeSlot.query
                     .order_by(TimeSlot.day_of_week, TimeSlot.start_time)
                     .all())

    day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    all_timeslots.sort(key=lambda ts: (
        day_order.index(ts.day_of_week), ts.start_time
    ))

    def _slot_ok(ts, s):
        start_m = ts.start_time.hour * 60 + ts.start_time.minute
        end_m   = ts.end_time.hour   * 60 + ts.end_time.minute
        if (end_m - start_m) // 60 != s.duration_hours:
            return False
        return ts.period_label.startswith('Lab') == (s.session_type == 'lab')

    compat_ts = {
        s.id: [ts for ts in all_timeslots if _slot_ok(ts, s)]
        for s in sessions
    }

    return render_template('admin/course_sessions.html',
                           course=course,
                           sessions=sessions,
                           professors=professors,
                           group_choices=group_choices,
                           compat_ts=compat_ts)


@admin_bp.route('/courses/<int:course_id>/sessions/<int:session_id>/assign',
                methods=['POST'])
@login_required
def session_assign(course_id, session_id):
    from app.models.class_session_professor import ClassSessionProfessor

    session = ClassSession.query.get_or_404(session_id)
    student_group_raw  = request.form.get('student_group_id', '').strip()
    fixed_ts_raw       = request.form.get('fixed_timeslot_id', '').strip()

    session.student_group_id  = int(student_group_raw) if student_group_raw else None
    session.fixed_timeslot_id = int(fixed_ts_raw)      if fixed_ts_raw      else None

    # Rebuild professor assignments from form
    primary_raw   = request.form.get('professor_id_primary', '').strip()
    co_raws       = request.form.getlist('professor_id_co')

    # Delete existing assignments
    ClassSessionProfessor.query.filter_by(session_id=session.id).delete()

    order = 0
    if primary_raw:
        db.session.add(ClassSessionProfessor(
            session_id=session.id, professor_id=int(primary_raw),
            is_primary=True, display_order=0
        ))
        order = 1
    for co_raw in co_raws:
        co_raw = co_raw.strip()
        if co_raw:
            db.session.add(ClassSessionProfessor(
                session_id=session.id, professor_id=int(co_raw),
                is_primary=False, display_order=order
            ))
            order += 1

    db.session.commit()
    flash('Session updated.', 'success')
    return redirect(url_for('admin.course_sessions', course_id=course_id))


# ---------------------------------------------------------------------------
# Availability Declarations — admin classification
# ---------------------------------------------------------------------------

@admin_bp.route('/declarations', methods=['GET', 'POST'])
@login_required
def declarations():
    if request.method == 'POST':
        decl_id         = request.form.get('decl_id', '').strip()
        constraint_type = request.form.get('constraint_type', '').strip()

        if decl_id and constraint_type in ('strict', 'preferred'):
            decl = AvailabilityDeclaration.query.get_or_404(int(decl_id))
            decl.constraint_type = constraint_type
            decl.status          = 'classified'
            db.session.commit()
            flash(
                f'{decl.professor.user.name} — {decl.timeslot.day_of_week} '
                f'{decl.timeslot.period_label} classified as {constraint_type.capitalize()}.',
                'success'
            )
        else:
            flash('Invalid classification request.', 'danger')

        return redirect(url_for('admin.declarations'))

    pending    = (AvailabilityDeclaration.query
                  .filter_by(status='pending')
                  .order_by(AvailabilityDeclaration.submitted_at)
                  .all())
    classified = (AvailabilityDeclaration.query
                  .filter_by(status='classified')
                  .order_by(AvailabilityDeclaration.submitted_at.desc())
                  .all())

    return render_template('admin/declarations.html',
                           pending=pending,
                           classified=classified)


# ---------------------------------------------------------------------------
# Manual timetable editing — helpers
# ---------------------------------------------------------------------------

DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']


def _slot_ok(ts, s):
    """Return True if timeslot ts is compatible with session s."""
    start_m = ts.start_time.hour * 60 + ts.start_time.minute
    end_m   = ts.end_time.hour   * 60 + ts.end_time.minute
    if (end_m - start_m) // 60 != s.duration_hours:
        return False
    return ts.period_label.startswith('Lab') == (s.session_type == 'lab')


def _check_week_conflicts(week_number, trimester, timeslot_id,
                          room_id, professor_id, student_group_id,
                          exclude_entry_id=None):
    """
    Check if placing a session at (week_number, timeslot_id) causes double-bookings.
    Returns a list of human-readable conflict strings. Empty list = no conflicts.
    """
    from app.engine.solver import _conflicting_group_ids
    conflicts = []

    q = (TimetableEntry.query
         .filter_by(trimester=trimester,
                    week_number=week_number,
                    timeslot_id=timeslot_id))
    if exclude_entry_id:
        q = q.filter(TimetableEntry.id != exclude_entry_id)
    competing = q.all()

    for other in competing:
        other_prof = other.effective_professor
        other_prof_id   = other_prof.id if other_prof else None
        other_group_id  = other.class_session.student_group_id

        if professor_id and other_prof_id == professor_id:
            conflicts.append(
                f'Professor double-booking: already assigned to '
                f'{other.class_session.course.module_code} '
                f'({other.class_session.session_type}) '
                f'at this slot in Week {week_number}.'
            )

        if room_id and other.room_id and other.room_id == room_id:
            conflicts.append(
                f'Room double-booking: {other.room.room_code} is already used by '
                f'{other.class_session.course.module_code} '
                f'at this slot in Week {week_number}.'
            )

        if student_group_id and other_group_id:
            if other_group_id in _conflicting_group_ids(student_group_id):
                other_label = other.class_session.student_group.group_label
                conflicts.append(
                    f'Student group double-booking: {other_label} is already in '
                    f'{other.class_session.course.module_code} '
                    f'at this slot in Week {week_number}.'
                )

    return conflicts


def _write_audit(user_id, trimester, action,
                 module_code, session_type, week_label,
                 old_ts, new_ts, old_room, new_room,
                 old_prof, new_prof):
    """Write one AuditLog entry."""
    db.session.add(AuditLog(
        user_id      = user_id,
        trimester    = trimester,
        action       = action,
        module_code  = module_code,
        session_type = session_type,
        week_label   = week_label,
        old_timeslot = old_ts,
        new_timeslot = new_ts,
        old_room     = old_room,
        new_room     = new_room,
        old_professor= old_prof,
        new_professor= new_prof,
    ))


# ---------------------------------------------------------------------------
# Manual timetable editing — routes
# ---------------------------------------------------------------------------

@admin_bp.route('/timetable/<trimester>/sessions/<int:session_id>/weeks')
@login_required
def timetable_session_weeks(trimester, session_id):
    session = ClassSession.query.get_or_404(session_id)

    entries = (TimetableEntry.query
               .filter_by(class_session_id=session_id, trimester=trimester)
               .order_by(TimetableEntry.week_number)
               .all())

    from app.models.academic_calendar import AcademicCalendar
    cal = {w.week_number: w for w in
           AcademicCalendar.query.filter_by(trimester=trimester).all()}

    return render_template('admin/timetable_session_weeks.html',
                           session=session,
                           entries=entries,
                           trimester=trimester,
                           cal=cal)


@admin_bp.route('/timetable/entries/<int:entry_id>/edit', methods=['GET', 'POST'])
@login_required
def timetable_edit_entry(entry_id):
    """Edit a single week's timetable entry."""
    entry   = TimetableEntry.query.get_or_404(entry_id)
    session = entry.class_session

    all_ts = TimeSlot.query.all()
    all_ts.sort(key=lambda ts: (DAY_ORDER.index(ts.day_of_week), ts.start_time))
    compat_slots = [ts for ts in all_ts if _slot_ok(ts, session)]

    all_rooms   = Room.query.filter_by(is_active=True).order_by(Room.room_code).all()
    professors  = Professor.query.join(Professor.user).order_by(User.name).all()

    conflicts   = []
    force_save  = False

    if request.method == 'POST':
        new_ts_id   = request.form.get('timeslot_id', '').strip()
        new_room_id = request.form.get('room_id', '').strip()
        new_prof_id = request.form.get('professor_id', '').strip()
        force_save  = request.form.get('force_save') == '1'

        errors = []
        if not new_ts_id:
            errors.append('Please select a timeslot.')

        if not errors:
            new_ts_id   = int(new_ts_id)
            new_room_id = int(new_room_id) if new_room_id else None
            new_prof_id = int(new_prof_id) if new_prof_id else None

            if not force_save:
                conflicts = _check_week_conflicts(
                    week_number      = entry.week_number,
                    trimester        = entry.trimester,
                    timeslot_id      = new_ts_id,
                    room_id          = new_room_id,
                    professor_id     = new_prof_id,
                    student_group_id = session.student_group_id,
                    exclude_entry_id = entry.id,
                )

            if not conflicts or force_save:
                # Build audit values
                old_ts_obj  = entry.timeslot
                new_ts_obj  = TimeSlot.query.get(new_ts_id)
                old_room_obj = entry.room
                new_room_obj = Room.query.get(new_room_id) if new_room_id else None
                old_prof_obj = entry.effective_professor
                new_prof_obj = Professor.query.get(new_prof_id) if new_prof_id else None

                def _ts_label(ts):
                    return f'{ts.day_of_week} {ts.period_label} ({ts.start_time.strftime("%H:%M")}–{ts.end_time.strftime("%H:%M")})' if ts else '—'

                _write_audit(
                    user_id      = current_user.id,
                    trimester    = entry.trimester,
                    action       = 'edit_week',
                    module_code  = session.course.module_code,
                    session_type = session.session_type,
                    week_label   = f'Week {entry.week_number}',
                    old_ts       = _ts_label(old_ts_obj),
                    new_ts       = _ts_label(new_ts_obj),
                    old_room     = old_room_obj.room_code if old_room_obj else 'Online',
                    new_room     = new_room_obj.room_code if new_room_obj else 'Online',
                    old_prof     = old_prof_obj.user.name if old_prof_obj else '—',
                    new_prof     = new_prof_obj.user.name if new_prof_obj else '—',
                )

                entry.timeslot_id           = new_ts_id
                entry.room_id               = new_room_id
                entry.override_professor_id = new_prof_id if new_prof_id != session.professor_id else None
                entry.is_manually_edited    = True
                db.session.commit()

                flash(f'Week {entry.week_number} updated successfully.', 'success')
                return redirect(url_for('admin.timetable_session_weeks',
                                        trimester=entry.trimester,
                                        session_id=session.id))
        else:
            for e in errors:
                flash(e, 'danger')

    return render_template('admin/timetable_edit_entry.html',
                           entry=entry,
                           session=session,
                           compat_slots=compat_slots,
                           all_rooms=all_rooms,
                           professors=professors,
                           conflicts=conflicts,
                           edit_mode='single')


@admin_bp.route('/timetable/<trimester>/sessions/<int:session_id>/edit-all',
                methods=['GET', 'POST'])
@login_required
def timetable_edit_all_weeks(trimester, session_id):
    """Edit all weeks for a session at once."""
    session = ClassSession.query.get_or_404(session_id)

    entries = (TimetableEntry.query
               .filter_by(class_session_id=session_id, trimester=trimester)
               .all())

    if not entries:
        flash('No timetable entries found for this session.', 'danger')
        return redirect(url_for('admin.timetable', trimester=trimester))

    all_ts = TimeSlot.query.all()
    all_ts.sort(key=lambda ts: (DAY_ORDER.index(ts.day_of_week), ts.start_time))
    compat_slots = [ts for ts in all_ts if _slot_ok(ts, session)]

    all_rooms  = Room.query.filter_by(is_active=True).order_by(Room.room_code).all()
    professors = Professor.query.join(Professor.user).order_by(User.name).all()

    # Use first entry as the representative current values
    rep         = entries[0]
    conflicts   = []
    force_save  = False

    if request.method == 'POST':
        new_ts_id   = request.form.get('timeslot_id', '').strip()
        new_room_id = request.form.get('room_id', '').strip()
        new_prof_id = request.form.get('professor_id', '').strip()
        force_save  = request.form.get('force_save') == '1'

        errors = []
        if not new_ts_id:
            errors.append('Please select a timeslot.')

        if not errors:
            new_ts_id   = int(new_ts_id)
            new_room_id = int(new_room_id) if new_room_id else None
            new_prof_id = int(new_prof_id) if new_prof_id else None

            if not force_save:
                for e in entries:
                    week_conflicts = _check_week_conflicts(
                        week_number      = e.week_number,
                        trimester        = trimester,
                        timeslot_id      = new_ts_id,
                        room_id          = new_room_id,
                        professor_id     = new_prof_id,
                        student_group_id = session.student_group_id,
                        exclude_entry_id = e.id,
                    )
                    conflicts.extend(week_conflicts)

            if not conflicts or force_save:
                old_ts_obj   = rep.timeslot
                new_ts_obj   = TimeSlot.query.get(new_ts_id)
                old_room_obj = rep.room
                new_room_obj = Room.query.get(new_room_id) if new_room_id else None
                old_prof_obj = rep.effective_professor
                new_prof_obj = Professor.query.get(new_prof_id) if new_prof_id else None

                def _ts_label(ts):
                    return f'{ts.day_of_week} {ts.period_label} ({ts.start_time.strftime("%H:%M")}–{ts.end_time.strftime("%H:%M")})' if ts else '—'

                _write_audit(
                    user_id      = current_user.id,
                    trimester    = trimester,
                    action       = 'edit_all_weeks',
                    module_code  = session.course.module_code,
                    session_type = session.session_type,
                    week_label   = 'All weeks',
                    old_ts       = _ts_label(old_ts_obj),
                    new_ts       = _ts_label(new_ts_obj),
                    old_room     = old_room_obj.room_code if old_room_obj else 'Online',
                    new_room     = new_room_obj.room_code if new_room_obj else 'Online',
                    old_prof     = old_prof_obj.user.name if old_prof_obj else '—',
                    new_prof     = new_prof_obj.user.name if new_prof_obj else '—',
                )

                for e in entries:
                    e.timeslot_id           = new_ts_id
                    e.room_id               = new_room_id
                    e.override_professor_id = new_prof_id if new_prof_id != session.professor_id else None
                    e.is_manually_edited    = True

                db.session.commit()
                flash(f'All weeks for {session.course.module_code} updated successfully.', 'success')
                return redirect(url_for('admin.timetable_session_weeks',
                                        trimester=trimester,
                                        session_id=session_id))
        else:
            for e in errors:
                flash(e, 'danger')

    return render_template('admin/timetable_edit_entry.html',
                           entry=rep,
                           session=session,
                           compat_slots=compat_slots,
                           all_rooms=all_rooms,
                           professors=professors,
                           conflicts=conflicts,
                           edit_mode='all',
                           trimester=trimester)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Historical slot preference helper
# ---------------------------------------------------------------------------

def _build_historical_preferred(academic_year, trimester_num):
    """
    Build {class_session_id: timeslot_id} for soft-constraint guidance.

    Priority:
      1. Backbone entries for this AY+trimester (when regenerating an existing AY).
      2. All entries from the previous AY's equivalent trimester (normal case).
    """
    if not academic_year or len(academic_year) < 6:
        return {}

    tri_key = f'{academic_year}-T{trimester_num}'

    # Priority 1: backbone entries in this AY tell us the "real" schedule to target
    backbone = (TimetableEntry.query
                .filter_by(trimester=tri_key, is_backbone=True)
                .all())
    if backbone:
        preferred = {}
        for e in backbone:
            if e.class_session_id not in preferred:
                preferred[e.class_session_id] = e.timeslot_id
        return preferred

    # Priority 2: previous AY's equivalent trimester
    try:
        y1 = int(academic_year[2:4])
        y2 = int(academic_year[4:6])
        prev_ay = f'AY{y1 - 1:02d}{y2 - 1:02d}'
    except ValueError:
        return {}

    prev_tri_key = f'{prev_ay}-T{trimester_num}'
    prev_entries = (TimetableEntry.query
                    .filter_by(trimester=prev_tri_key)
                    .all())
    preferred = {}
    for e in prev_entries:
        if e.class_session_id not in preferred:
            preferred[e.class_session_id] = e.timeslot_id
    return preferred


# ---------------------------------------------------------------------------
# Timetable Flags
# ---------------------------------------------------------------------------

def _auto_create_flags(trimester, preferred_violations):
    """
    After solver run, create TimetableFlag records for each preferred violation.
    Skips violations that already have an open/acknowledged flag to avoid duplicates.
    Clears resolved flags for this trimester first (fresh run).
    """
    from app.models.timetable_flag import TimetableFlag

    # Delete any previously open flags for this trimester (solver re-run resets them)
    TimetableFlag.query.filter(
        TimetableFlag.status.in_(['open']),
        TimetableFlag.timetable_entry.has(trimester=trimester)
    ).delete(synchronize_session='fetch')

    for v in preferred_violations:
        if not v.get('declaration_id'):
            continue
        rep_entry = TimetableEntry.query.filter_by(
            class_session_id=v['class_session_id'],
            trimester=trimester,
        ).first()
        if rep_entry:
            db.session.add(TimetableFlag(
                timetable_entry_id = rep_entry.id,
                professor_id       = v['professor_id'],
                declaration_id     = v['declaration_id'],
                status             = 'open',
            ))
    db.session.commit()


@admin_bp.route('/timetable-flags')
@login_required
def timetable_flags():
    from app.models.timetable_flag import TimetableFlag

    trimester_filter = request.args.get('trimester', '')

    trimesters = [r[0] for r in
                  db.session.query(TimetableEntry.trimester).distinct()
                  .order_by(TimetableEntry.trimester).all()]

    # Default to most recent trimester
    if not trimester_filter and trimesters:
        trimester_filter = trimesters[-1]

    flags_query = (TimetableFlag.query
                   .join(TimetableFlag.timetable_entry)
                   .filter(TimetableEntry.trimester == trimester_filter)
                   .order_by(TimetableFlag.created_at.desc()))

    open_flags         = flags_query.filter(TimetableFlag.status == 'open').all()
    acknowledged_flags = flags_query.filter(TimetableFlag.status == 'acknowledged').all()
    resolved_flags     = flags_query.filter(TimetableFlag.status == 'resolved').all()

    return render_template('admin/timetable_flags.html',
                           open_flags=open_flags,
                           acknowledged_flags=acknowledged_flags,
                           resolved_flags=resolved_flags,
                           trimesters=trimesters,
                           active_trimester=trimester_filter)


@admin_bp.route('/timetable-flags/<int:flag_id>/notify', methods=['POST'])
@login_required
def flag_notify(flag_id):
    """Admin sets response deadline and marks notification as sent (email wired in Step 8)."""
    from app.models.timetable_flag import TimetableFlag
    from datetime import date as date_type

    flag     = TimetableFlag.query.get_or_404(flag_id)
    deadline = request.form.get('response_deadline', '').strip()

    if not deadline:
        flash('Please set a response deadline before sending.', 'danger')
        return redirect(url_for('admin.timetable_flags',
                                trimester=flag.timetable_entry.trimester))

    flag.response_deadline = date_type.fromisoformat(deadline)
    db.session.commit()

    # Send email to professor
    from app.utils.email import send_flag_notification
    success, error = send_flag_notification(flag)

    if success:
        flag.notification_sent = True
        db.session.commit()
        flash(
            f'Email sent to {flag.professor.user.name} '
            f'({flag.professor.user.email}). '
            f'Deadline: {flag.response_deadline.strftime("%d %b %Y")}.',
            'success'
        )
    else:
        flash(
            f'Deadline saved but email could not be sent to '
            f'{flag.professor.user.name}. '
            f'SMTP error: {error}. '
            f'Please check your email configuration in config.py.',
            'danger'
        )

    return redirect(url_for('admin.timetable_flags',
                            trimester=flag.timetable_entry.trimester))


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

@admin_bp.route('/audit-log')
@login_required
def audit_log():
    trimester_filter = request.args.get('trimester', '')

    trimesters = [r[0] for r in
                  db.session.query(AuditLog.trimester).distinct()
                  .order_by(AuditLog.trimester).all()]

    query = AuditLog.query.order_by(AuditLog.timestamp.desc())
    if trimester_filter:
        query = query.filter_by(trimester=trimester_filter)

    logs = query.all()

    return render_template('admin/audit_log.html',
                           logs=logs,
                           trimesters=trimesters,
                           active_trimester=trimester_filter)


@admin_bp.route('/audit-log/export')
@login_required
def audit_log_export():
    """Download audit log as CSV."""
    import csv
    import io
    from flask import Response

    trimester_filter = request.args.get('trimester', '')
    query = AuditLog.query.order_by(AuditLog.timestamp.desc())
    if trimester_filter:
        query = query.filter_by(trimester=trimester_filter)
    logs = query.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Timestamp', 'Admin', 'Trimester', 'Module', 'Session Type',
                     'Scope', 'Old Timeslot', 'New Timeslot',
                     'Old Room', 'New Room', 'Old Professor', 'New Professor'])
    for log in logs:
        writer.writerow([
            log.timestamp.strftime('%Y-%m-%d %H:%M'),
            log.user.name if log.user else '',
            log.trimester or '',
            log.module_code or '',
            log.session_type or '',
            'All weeks' if log.action == 'edit_all_weeks' else (log.week_label or ''),
            log.old_timeslot or '', log.new_timeslot or '',
            log.old_room or '',    log.new_room or '',
            log.old_professor or '', log.new_professor or '',
        ])

    filename = f'audit_log{"_" + trimester_filter if trimester_filter else ""}.csv'
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


# ---------------------------------------------------------------------------
# Data Import / Export
# ---------------------------------------------------------------------------

@admin_bp.route('/import', methods=['GET', 'POST'])
@login_required
def data_import():
    import io, pandas as pd
    from flask import Response, send_file
    from app.models.user import User
    from werkzeug.security import generate_password_hash

    results = {}   # {type: {'created': n, 'updated': n, 'errors': [...]}}

    # ── Template downloads ──────────────────────────────────────────────────
    download = request.args.get('download', '')
    if download == 'rooms':
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            pd.DataFrame([
                {'room_code': 'E6-04-15', 'building': 'E6', 'capacity': 30,
                 'room_type': 'lab', 'is_active': 'TRUE'},
                {'room_code': 'E2-01-01', 'building': 'E2', 'capacity': 80,
                 'room_type': 'lecture', 'is_active': 'TRUE'},
            ]).to_excel(writer, index=False, sheet_name='Rooms')
        output.seek(0)
        return send_file(output, download_name='rooms_template.xlsx',
                         as_attachment=True,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    if download == 'professors':
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            pd.DataFrame([
                {'staff_id': 'P001', 'name': 'John Doe',
                 'email': 'john.doe@singaporetech.edu.sg', 'department': 'DSC'},
            ]).to_excel(writer, index=False, sheet_name='Professors')
        output.seek(0)
        return send_file(output, download_name='professors_template.xlsx',
                         as_attachment=True,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    if download == 'modules':
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            pd.DataFrame([
                {'programme_code': 'DSC', 'module_code': 'DSC1001',
                 'title': 'Introduction to Data Science',
                 'year_level': 1, 'trimester': 1,
                 'course_delivery_mode': 'f2f',
                 'session_type': 'lecture', 'session_delivery_mode': 'f2f',
                 'duration_hours': 2, 'sessions_per_week': 1,
                 'total_hours': 24, 'split_count': ''},
                {'programme_code': 'DSC', 'module_code': 'DSC1001',
                 'title': 'Introduction to Data Science',
                 'year_level': 1, 'trimester': 1,
                 'course_delivery_mode': 'f2f',
                 'session_type': 'lab', 'session_delivery_mode': 'f2f',
                 'duration_hours': 3, 'sessions_per_week': 1,
                 'total_hours': 24, 'split_count': 2},
            ]).to_excel(writer, index=False, sheet_name='Modules')
        output.seek(0)
        return send_file(output, download_name='modules_template.xlsx',
                         as_attachment=True,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    # ── File uploads ────────────────────────────────────────────────────────
    if request.method == 'POST':
        import_type = request.form.get('import_type', '')
        file = request.files.get('file')

        if not file or not file.filename.endswith(('.xlsx', '.xls')):
            flash('Please upload a valid Excel file (.xlsx or .xls).', 'danger')
            return redirect(url_for('admin.data_import'))

        try:
            df = pd.read_excel(file, dtype=str).fillna('')
        except Exception as e:
            flash(f'Could not read file: {e}', 'danger')
            return redirect(url_for('admin.data_import'))

        created = updated = 0
        errors = []

        # ── Rooms ──────────────────────────────────────────────────────────
        if import_type == 'rooms':
            required = {'room_code', 'building', 'capacity', 'room_type'}
            missing = required - set(df.columns.str.strip().str.lower())
            if missing:
                flash(f'Missing columns: {", ".join(missing)}', 'danger')
                return redirect(url_for('admin.data_import'))

            df.columns = df.columns.str.strip().str.lower()
            for i, row in df.iterrows():
                code = row.get('room_code', '').strip()
                if not code:
                    continue
                try:
                    cap = int(row.get('capacity', 0))
                    rtype = row.get('room_type', 'lecture').strip().lower()
                    if rtype not in ('lecture', 'lab', 'seminar'):
                        rtype = 'lecture'
                    active = str(row.get('is_active', 'TRUE')).strip().upper() != 'FALSE'
                    building = row.get('building', '').strip()

                    existing = Room.query.filter_by(room_code=code).first()
                    if existing:
                        existing.capacity = cap
                        existing.room_type = rtype
                        existing.is_active = active
                        existing.building = building
                        updated += 1
                    else:
                        db.session.add(Room(room_code=code, building=building,
                                           capacity=cap, room_type=rtype, is_active=active))
                        created += 1
                except Exception as e:
                    errors.append(f'Row {i+2} ({code}): {e}')

            db.session.commit()
            results['rooms'] = {'created': created, 'updated': updated, 'errors': errors}

        # ── Professors ─────────────────────────────────────────────────────
        elif import_type == 'professors':
            required = {'staff_id', 'name', 'email', 'department'}
            missing = required - set(df.columns.str.strip().str.lower())
            if missing:
                flash(f'Missing columns: {", ".join(missing)}', 'danger')
                return redirect(url_for('admin.data_import'))

            df.columns = df.columns.str.strip().str.lower()
            for i, row in df.iterrows():
                sid = row.get('staff_id', '').strip()
                if not sid:
                    continue
                try:
                    name  = row.get('name', '').strip()
                    email = row.get('email', '').strip()
                    dept  = row.get('department', '').strip()

                    existing = Professor.query.filter_by(staff_id=sid).first()
                    if existing:
                        existing.department = dept
                        existing.user.name  = name
                        existing.user.email = email
                        updated += 1
                    else:
                        if User.query.filter_by(email=email).first():
                            errors.append(f'Row {i+2} ({sid}): email {email} already in use')
                            continue
                        user = User(name=name, email=email, role='professor',
                                    password=generate_password_hash('password'))
                        db.session.add(user)
                        db.session.flush()
                        db.session.add(Professor(user_id=user.id, staff_id=sid, department=dept))
                        created += 1
                except Exception as e:
                    errors.append(f'Row {i+2} ({sid}): {e}')

            db.session.commit()
            results['professors'] = {'created': created, 'updated': updated, 'errors': errors}

        # ── Modules ────────────────────────────────────────────────────────
        elif import_type == 'modules':
            required = {'programme_code', 'module_code', 'title', 'year_level',
                        'session_type', 'session_delivery_mode', 'duration_hours'}
            missing = required - set(df.columns.str.strip().str.lower())
            if missing:
                flash(f'Missing columns: {", ".join(missing)}', 'danger')
                return redirect(url_for('admin.data_import'))

            df.columns = df.columns.str.strip().str.lower()
            for i, row in df.iterrows():
                mc = row.get('module_code', '').strip().upper()
                if not mc:
                    continue
                try:
                    prog_code = row.get('programme_code', '').strip().upper()
                    prog = Programme.query.filter_by(code=prog_code).first()
                    if not prog:
                        errors.append(f'Row {i+2} ({mc}): programme "{prog_code}" not found')
                        continue

                    stype = row.get('session_type', 'lecture').strip().lower()
                    s_delivery = row.get('session_delivery_mode', 'f2f').strip().lower()
                    dur = int(row.get('duration_hours', 2))
                    tri = int(row.get('trimester', 0)) or None
                    yr = int(row.get('year_level', 1))
                    c_delivery = row.get('course_delivery_mode', s_delivery).strip().lower()
                    spw = int(row.get('sessions_per_week', 1))
                    total_h = int(row.get('total_hours', dur * 12))
                    split_raw = row.get('split_count', '').strip()
                    split = int(split_raw) if split_raw.isdigit() else None

                    course = Course.query.filter_by(
                        module_code=mc, programme_id=prog.id
                    ).first()
                    if not course:
                        title = row.get('title', mc).strip()
                        course = Course(
                            programme_id=prog.id, module_code=mc, title=title,
                            year_level=yr, trimester=tri, delivery_mode=c_delivery,
                            sessions_per_week=spw, total_hours=total_h, split_count=split,
                        )
                        db.session.add(course)
                        db.session.flush()
                        created += 1

                    # Add session if this exact type doesn't exist yet
                    existing_sess = ClassSession.query.filter_by(
                        course_id=course.id, session_type=stype
                    ).first()
                    if not existing_sess:
                        db.session.add(ClassSession(
                            course_id=course.id, session_type=stype,
                            delivery_mode=s_delivery, duration_hours=dur,
                        ))
                    else:
                        updated += 1

                except Exception as e:
                    errors.append(f'Row {i+2} ({mc}): {e}')

            db.session.commit()
            results['modules'] = {'created': created, 'updated': updated, 'errors': errors}

        if not results:
            flash('Nothing was imported.', 'warning')
        else:
            for itype, r in results.items():
                flash(
                    f'{itype.title()}: {r["created"]} created, {r["updated"]} updated'
                    + (f', {len(r["errors"])} errors' if r["errors"] else '') + '.',
                    'success' if not r["errors"] else 'warning'
                )

    return render_template('admin/data_import.html', results=results)


# ---------------------------------------------------------------------------
# Template 1 import — upload → preview → confirm
# ---------------------------------------------------------------------------

@admin_bp.route('/import/template1', methods=['GET', 'POST'])
@login_required
def import_template1():
    import os, uuid, re, tempfile
    import pandas as pd
    from app.engine.template1_parser import (
        load_module_sheet, PROG_NAMES, SKIP_NAMES, SKIP_SHEETS,
    )

    UPLOAD_DIR = os.path.join(tempfile.gettempdir(), 'sit_template1_uploads')
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    def _clean_staff(staff_list):
        out = []
        for name, sid in staff_list:
            n = '' if (name is None or (isinstance(name, float) and math.isnan(name))) else str(name).strip()
            s = '' if (sid is None or (isinstance(sid, float) and math.isnan(sid))) else str(sid).strip()
            if n and n.lower() not in SKIP_NAMES:
                out.append({'name': n, 'sid': s})
        return out

    def _get_or_create_programme(code):
        prog = Programme.query.filter_by(code=code).first()
        if not prog:
            prog = Programme(code=code, name=PROG_NAMES.get(code, code), cluster='ENG')
            db.session.add(prog)
            db.session.flush()
        return prog

    def _get_or_create_student_group(prog, year_level, intake_size):
        label = f'{prog.code}-Y{year_level}'
        sg = StudentGroup.query.filter_by(
            programme_id=prog.id, year_level=year_level,
            group_label=label, parent_id=None,
        ).first()
        if not sg:
            sg = StudentGroup(
                programme_id=prog.id, year_level=year_level,
                group_label=label, intake_size=intake_size or 30, parent_id=None,
            )
            db.session.add(sg)
            db.session.flush()
        elif intake_size and intake_size > 0 and sg.intake_size != intake_size:
            sg.intake_size = intake_size
        return sg

    def _get_or_create_professor(name_raw, sid_raw):
        name = '' if (name_raw is None or (isinstance(name_raw, float) and math.isnan(name_raw))) else str(name_raw).strip()
        sid  = '' if (sid_raw  is None or (isinstance(sid_raw,  float) and math.isnan(sid_raw)))  else str(sid_raw).strip()
        if not name or name.lower() in SKIP_NAMES:
            return None
        if re.fullmatch(r't\d{1,2}', name.lower()):
            return None
        name = name.title()
        sid  = re.sub(r'[^\w]', '', sid)
        if re.fullmatch(r't\d{1,2}', sid, re.IGNORECASE):
            sid = ''
        if sid:
            prof = Professor.query.filter_by(staff_id=sid).first()
            if prof:
                return prof
        user = User.query.filter_by(name=name, role='professor').first()
        if user:
            prof = Professor.query.filter_by(user_id=user.id).first()
            if prof:
                if sid and not prof.staff_id:
                    prof.staff_id = sid
                return prof
        if not sid:
            base = abs(hash(name)) % 9999
            sid = f'ENG{base:04d}'
            attempt = 0
            while Professor.query.filter_by(staff_id=sid).first():
                attempt += 1
                sid = f'ENG{(base + attempt) % 9999:04d}'
        email_local = re.sub(r'[^a-z0-9]', '.', name.lower()).strip('.')
        email_local = re.sub(r'\.+', '.', email_local)
        email = f'{email_local}@sit.edu.sg'
        attempt = 0
        while User.query.filter_by(email=email).first():
            attempt += 1
            email = f'{email_local}.{attempt}@sit.edu.sg'
        user = User(name=name, email=email, role='professor')
        user.set_password('SIT@2526')
        db.session.add(user)
        db.session.flush()
        prof = Professor(user_id=user.id, staff_id=sid, department='ENG')
        db.session.add(prof)
        db.session.flush()
        return prof

    def _get_or_create_course(module_code, title, prog, year_level, delivery_mode, tri):
        course = Course.query.filter_by(
            module_code=module_code, programme_id=prog.id, trimester=tri,
        ).first()
        if not course:
            course = Course(
                programme_id=prog.id, module_code=module_code,
                title=title or module_code, year_level=year_level,
                trimester=tri, delivery_mode=delivery_mode,
                sessions_per_week=1, total_hours=0,
            )
            db.session.add(course)
            db.session.flush()
        return course

    def _parse_file(fpath, fname, all_slots):
        xl = pd.ExcelFile(fpath)
        sessions = []
        for sheet in xl.sheet_names:
            if sheet.strip().lower() in SKIP_SHEETS:
                continue
            try:
                df_raw = pd.read_excel(fpath, sheet_name=sheet, header=None)
            except Exception:
                continue
            for rec in load_module_sheet(df_raw, all_slots, fname_hint=fname):
                sessions.append(rec)
        return sessions

    if request.method == 'GET':
        return render_template('admin/import_template1.html')

    trimester    = int(request.form.get('trimester', 1))
    confirm      = request.form.get('confirm') == '1'
    token        = request.form.get('token', '').strip()
    orig_filename = request.form.get('orig_filename', '')

    if confirm and token:
        fpath = os.path.join(UPLOAD_DIR, f'{token}.xlsx')
        if not os.path.exists(fpath):
            flash('Upload session expired — please re-upload.', 'danger')
            return redirect(url_for('admin.import_template1'))

        all_slots = TimeSlot.query.all()
        recs = _parse_file(fpath, orig_filename, all_slots)

        seen = set()
        for cs in ClassSession.query.join(Course).all():
            seen.add((cs.course.module_code, cs.course.programme_id,
                      cs.session_type, cs.group_label or 'All', cs.teaching_weeks or ''))

        created = 0
        skipped = 0
        try:
            for rec in recs:
                if not rec['prog_code'] or not rec['year_level']:
                    skipped += 1
                    continue
                prog   = _get_or_create_programme(rec['prog_code'])
                sg     = _get_or_create_student_group(prog, rec['year_level'], rec['class_size'])
                course = _get_or_create_course(
                    rec['module_code'], rec['module_title'],
                    prog, rec['year_level'], rec['delivery_mode'], trimester,
                )
                sig = (rec['module_code'], prog.id, rec['session_type'],
                       rec['group_label'], rec['teaching_weeks'] or '')
                if sig in seen:
                    skipped += 1
                    continue
                seen.add(sig)
                cs = ClassSession(
                    course_id=course.id,
                    session_type=rec['session_type'],
                    delivery_mode=rec['delivery_mode'],
                    is_async=rec['is_async'],
                    duration_hours=rec['duration_hours'],
                    student_group_id=sg.id,
                    trimester=trimester,
                    teaching_weeks=rec['teaching_weeks'],
                    group_label=rec['group_label'],
                    preferred_timeslot_id=rec['pref_slot_id'],
                )
                db.session.add(cs)
                db.session.flush()
                created += 1
                is_first = True
                for staff_name, staff_id in rec['staff']:
                    prof = _get_or_create_professor(staff_name, staff_id)
                    if not prof:
                        continue
                    already = ClassSessionProfessor.query.filter_by(
                        session_id=cs.id, professor_id=prof.id).first()
                    if not already:
                        db.session.add(ClassSessionProfessor(
                            session_id=cs.id, professor_id=prof.id,
                            is_primary=is_first,
                            display_order=0 if is_first else 1,
                        ))
                        if is_first:
                            is_first = False
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(f'Import failed: {e}', 'danger')
            return redirect(url_for('admin.import_template1'))

        try:
            os.unlink(fpath)
        except OSError:
            pass

        flash(f'Import complete: {created} sessions created, {skipped} skipped.', 'success')
        return redirect(url_for('admin.import_template1'))

    # Step 1: upload + parse for preview
    file = request.files.get('file')
    if not file or not file.filename:
        flash('Please select a file.', 'danger')
        return render_template('admin/import_template1.html', trimester=trimester)

    token = str(uuid.uuid4())
    fpath = os.path.join(UPLOAD_DIR, f'{token}.xlsx')
    file.save(fpath)

    all_slots = TimeSlot.query.all()
    try:
        recs = _parse_file(fpath, file.filename, all_slots)
    except Exception as e:
        try:
            os.unlink(fpath)
        except OSError:
            pass
        flash(f'Could not parse file: {e}', 'danger')
        return render_template('admin/import_template1.html', trimester=trimester)

    preview = []
    warn_count = 0
    for rec in recs:
        staff_clean = _clean_staff(rec['staff'])
        row_warnings = []
        if not staff_clean:
            row_warnings.append('No staff assigned')
        if rec['prog_code'] not in PROG_NAMES:
            row_warnings.append(f'Unknown programme code: {rec["prog_code"]}')
        if not rec['teaching_weeks']:
            row_warnings.append('Teaching weeks not specified')
        if row_warnings:
            warn_count += 1
        preview.append({
            'module_code':    rec['module_code'],
            'module_title':   rec['module_title'],
            'session_type':   rec['session_type'],
            'group_label':    rec['group_label'],
            'duration_hours': rec['duration_hours'],
            'teaching_weeks': rec['teaching_weeks'] or '—',
            'is_async':       rec['is_async'],
            'delivery_mode':  rec['delivery_mode'],
            'prog_code':      rec['prog_code'],
            'year_level':     rec['year_level'],
            'staff':          staff_clean,
            'pref_slot_id':   rec['pref_slot_id'],
            'warnings':       row_warnings,
        })

    return render_template('admin/import_template1.html',
                           preview=preview,
                           warn_count=warn_count,
                           token=token,
                           orig_filename=file.filename,
                           trimester=trimester)


# ---------------------------------------------------------------------------
# Timetable — async solver (background task + polling)
# ---------------------------------------------------------------------------

def _purge_old_tasks():
    """Remove task entries older than _TASK_TTL to keep the dict tidy."""
    cutoff = _time.time() - _TASK_TTL
    stale = [tid for tid, t in _solver_tasks.items() if t['started_at'] < cutoff]
    for tid in stale:
        _solver_tasks.pop(tid, None)


def _run_solver_task(app, task_id, action, form_data):
    """Background thread: runs solver and writes result into _solver_tasks."""
    from datetime import date as _date
    from app.engine.solver import solve as _solve
    from app.engine.checker import get_blocking_issues

    def _update(status, message, **kw):
        _solver_tasks[task_id].update({'status': status, 'message': message, **kw})

    with app.app_context():
        try:
            academic_year = form_data.get('academic_year', '').strip().upper()
            break_raw = form_data.get('term_break_weeks', '7').strip()
            term_break_weeks = {int(p) for p in break_raw.split(',') if p.strip().isdigit()} or {7}
            preserve = form_data.get('preserve_existing') == 'on'

            if action == 'generate':
                tri_raw = form_data.get('trimester_num', '').strip()
                trimester_num = int(tri_raw) if tri_raw in ('1', '2', '3') else None
                trimester = f'{academic_year}-T{trimester_num}' if trimester_num else ''
                start_raw = form_data.get('start_date', '').strip()
                if not start_raw and academic_year and trimester_num:
                    start_raw = SIT_ACADEMIC_CALENDAR.get(academic_year, {}).get(trimester_num, '')

                _update('running', f'Solving {trimester}…', trimester=trimester)

                pinned_slots = None
                if preserve:
                    existing = TimetableEntry.query.filter_by(trimester=trimester, is_backbone=False).all()
                    seen = set()
                    pinned_slots = {e.class_session_id: e.timeslot_id
                                    for e in existing if e.class_session_id not in seen
                                    and not seen.add(e.class_session_id)} or None

                historical_preferred = _build_historical_preferred(academic_year, trimester_num)

                bb_entries = TimetableEntry.query.filter_by(trimester=trimester, is_backbone=True).all()
                if bb_entries:
                    backbone_pins = {e.class_session_id: e.timeslot_id for e in bb_entries}
                    if pinned_slots:
                        pinned_slots.update(backbone_pins)
                    else:
                        pinned_slots = backbone_pins

                success, message, stats = _solve(
                    trimester, _date.fromisoformat(start_raw), term_break_weeks,
                    trimester_num=trimester_num, academic_year=academic_year,
                    pinned_slots=pinned_slots, historical_preferred=historical_preferred,
                )
                if success:
                    _auto_create_flags(trimester, stats.get('preferred_violations', []))
                _update('done', message, success=success, stats=stats,
                        trimester=trimester, academic_year=academic_year)

            elif action == 'generate_ay':
                ay_stats = {}
                any_success = False
                for tri_num in [1, 2, 3]:
                    tri_key = f'{academic_year}-T{tri_num}'
                    sd_raw = form_data.get(f'start_date_t{tri_num}', '').strip()
                    if not sd_raw:
                        sd_raw = SIT_ACADEMIC_CALENDAR.get(academic_year, {}).get(tri_num, '')
                    if not sd_raw:
                        ay_stats[tri_num] = {'success': False, 'message': f'Tri {tri_num}: no start date — skipped.', 'stats': {}}
                        continue

                    tri_blockers, _ = get_blocking_issues(trimester_num=tri_num)
                    if tri_blockers:
                        ay_stats[tri_num] = {'success': False, 'message': f'Tri {tri_num}: {len(tri_blockers)} blocking issue(s) — skipped.', 'stats': {}}
                        continue

                    _update('running', f'Solving {tri_key} ({tri_num}/3)…',
                            trimester=f'{academic_year}-T1', academic_year=academic_year,
                            ay_stats=ay_stats)

                    pinned_slots = None
                    if preserve:
                        existing = TimetableEntry.query.filter_by(trimester=tri_key, is_backbone=False).all()
                        seen = set()
                        pinned_slots = {e.class_session_id: e.timeslot_id
                                        for e in existing if e.class_session_id not in seen
                                        and not seen.add(e.class_session_id)} or None

                    historical_preferred = _build_historical_preferred(academic_year, tri_num)
                    bb_entries = TimetableEntry.query.filter_by(trimester=tri_key, is_backbone=True).all()
                    if bb_entries:
                        backbone_pins = {e.class_session_id: e.timeslot_id for e in bb_entries}
                        if pinned_slots:
                            pinned_slots.update(backbone_pins)
                        else:
                            pinned_slots = backbone_pins

                    success, message, s = _solve(
                        tri_key, _date.fromisoformat(sd_raw), term_break_weeks,
                        trimester_num=tri_num, academic_year=academic_year,
                        pinned_slots=pinned_slots, historical_preferred=historical_preferred,
                    )
                    if success:
                        any_success = True
                        _auto_create_flags(tri_key, s.get('preferred_violations', []))
                    ay_stats[tri_num] = {'success': success, 'message': message, 'stats': s}

                summary = f'{academic_year}: Tri {", ".join(str(t) for t, v in ay_stats.items() if v["success"])} generated.'
                _update('done', summary if any_success else 'No trimesters were generated.',
                        success=any_success, stats={}, ay_stats=ay_stats,
                        trimester=f'{academic_year}-T1', academic_year=academic_year)

        except Exception as exc:
            _solver_tasks[task_id].update({
                'status': 'error',
                'success': False,
                'message': f'Solver error: {exc}',
            })


@admin_bp.route('/timetable/solve-async', methods=['POST'])
@login_required
def timetable_solve_async():
    """Start a background solver run and return a task_id for polling."""
    from app.engine.checker import get_blocking_issues
    from flask import current_app

    _purge_old_tasks()

    action = request.form.get('action', '')
    if action not in ('generate', 'generate_ay'):
        return jsonify({'error': 'Invalid action.'}), 400

    # Quick validation before spawning thread
    academic_year = request.form.get('academic_year', '').strip().upper()
    if not academic_year:
        return jsonify({'error': 'Academic year is required.'}), 400

    if action == 'generate':
        tri_raw = request.form.get('trimester_num', '').strip()
        trimester_num = int(tri_raw) if tri_raw in ('1', '2', '3') else None
        if not trimester_num:
            return jsonify({'error': 'Trimester number is required.'}), 400
        tri_blockers, _ = get_blocking_issues(trimester_num=trimester_num)
        if tri_blockers:
            return jsonify({'error': f'{len(tri_blockers)} blocking issue(s): ' + tri_blockers[0]}), 400
        start_raw = request.form.get('start_date', '').strip() or \
                    SIT_ACADEMIC_CALENDAR.get(academic_year, {}).get(trimester_num, '')
        if not start_raw:
            return jsonify({'error': 'Start date is required.'}), 400
        trimester = f'{academic_year}-T{trimester_num}'
    else:
        trimester = f'{academic_year}-T1'

    # Check no task already running for this trimester
    for t in _solver_tasks.values():
        if t.get('status') == 'running' and t.get('academic_year') == academic_year:
            return jsonify({'error': f'A solver run for {academic_year} is already in progress.'}), 409

    task_id = str(uuid.uuid4())
    _solver_tasks[task_id] = {
        'status': 'running',
        'message': 'Starting…',
        'started_at': _time.time(),
        'success': None,
        'stats': {},
        'trimester': trimester,
        'academic_year': academic_year,
    }

    form_data = request.form.to_dict()  # flat snapshot before the request context closes
    app = current_app._get_current_object()
    t = threading.Thread(target=_run_solver_task, args=(app, task_id, action, form_data), daemon=True)
    t.start()

    return jsonify({'task_id': task_id, 'trimester': trimester})


@admin_bp.route('/solver/status/<task_id>')
@login_required
def solver_status(task_id):
    """Poll endpoint for async solver progress."""
    task = _solver_tasks.get(task_id)
    if not task:
        return jsonify({'status': 'unknown', 'message': 'Task not found.'}), 404
    elapsed = int(_time.time() - task['started_at'])
    return jsonify({
        'status':        task['status'],
        'message':       task['message'],
        'elapsed':       elapsed,
        'success':       task.get('success'),
        'stats':         task.get('stats', {}),
        'trimester':     task.get('trimester', ''),
        'academic_year': task.get('academic_year', ''),
        'ay_stats':      task.get('ay_stats', {}),
    })


# ---------------------------------------------------------------------------
# Timetable — generate and view
# ---------------------------------------------------------------------------

@admin_bp.route('/timetable', methods=['GET', 'POST'])
@login_required
def timetable():
    from app.engine.checker import get_blocking_issues
    from app.engine.solver import solve
    from datetime import date

    issues, issue_warnings = get_blocking_issues()
    result   = None
    stats    = {}
    trimester = request.args.get('trimester', '')
    source    = request.args.get('source', '')   # 'bb' | 'generated' | ''

    # On fresh GET with no trimester param, default to T1 of the most recent AY
    if not trimester and request.method == 'GET':
        _latest = (db.session.query(TimetableEntry.trimester)
                   .distinct()
                   .order_by(TimetableEntry.trimester.desc())
                   .first())
        if _latest:
            trimester = f'{_latest[0][:6]}-all'

    # Compute default AY for the form (SIT AY starts August)
    today = date.today()
    if today.month >= 8:
        ay_default = f'AY{str(today.year)[2:]}{str(today.year + 1)[2:]}'
    else:
        ay_default = f'AY{str(today.year - 1)[2:]}{str(today.year)[2:]}'

    def _next_ay(ay):
        s, e = int(ay[2:4]), int(ay[4:6])
        return f'AY{s+1:02d}{e+1:02d}'

    selected_tri = None   # remembered for re-populating the form after POST
    ay_stats     = {}    # {tri_num: {'success', 'message', 'stats'}} for Full AY run

    if request.method == 'POST':
        action    = request.form.get('action', '')
        trimester = request.form.get('trimester', '').strip()
        start_raw = request.form.get('start_date', '').strip()

        if action == 'generate_ay':
            # ---------------------------------------------------------------
            # Full Academic Year — run solver for Tri 1, 2, 3 sequentially
            # ---------------------------------------------------------------
            academic_year = request.form.get('academic_year', '').strip().upper()
            preserve      = request.form.get('preserve_existing') == 'on'
            break_raw     = request.form.get('term_break_weeks', '7').strip()
            term_break_weeks = set()
            for part in break_raw.split(','):
                part = part.strip()
                if part.isdigit():
                    term_break_weeks.add(int(part))
            if not term_break_weeks:
                term_break_weeks = {7}

            if not academic_year:
                flash('Academic year is required (e.g. AY2526).', 'danger')
            else:
                any_success = False
                for tri_num in [1, 2, 3]:
                    tri_key  = f'{academic_year}-T{tri_num}'
                    sd_raw   = request.form.get(f'start_date_t{tri_num}', '').strip()
                    if not sd_raw:
                        # Fall back to official SIT calendar
                        sd_raw = SIT_ACADEMIC_CALENDAR.get(academic_year, {}).get(tri_num, '')
                    if not sd_raw:
                        ay_stats[tri_num] = {
                            'success': False,
                            'message': f'Tri {tri_num}: no start date — skipped (not in known calendar).',
                            'stats'  : {},
                        }
                        continue

                    tri_blockers, _tri_warns = get_blocking_issues(trimester_num=tri_num)
                    if tri_blockers:
                        ay_stats[tri_num] = {
                            'success': False,
                            'message': f'Tri {tri_num}: {len(tri_blockers)} blocking issue(s) — skipped. '
                                       f'({tri_blockers[0]}...)',
                            'stats'  : {},
                        }
                        continue

                    try:
                        start_date = date.fromisoformat(sd_raw)
                        pinned_slots = None
                        if preserve:
                            existing = TimetableEntry.query.filter_by(trimester=tri_key, is_backbone=False).all()
                            seen_sess = set()
                            pinned_slots = {}
                            for e in existing:
                                if e.class_session_id not in seen_sess:
                                    seen_sess.add(e.class_session_id)
                                    pinned_slots[e.class_session_id] = e.timeslot_id
                            if not pinned_slots:
                                pinned_slots = None

                        historical_preferred = _build_historical_preferred(academic_year, tri_num)

                        # Hard-pin backbone sessions so generated == backbone (1:1 similarity).
                        # Pins are dropped silently if incompatible or professor-blocked.
                        bb_entries = TimetableEntry.query.filter_by(
                            trimester=tri_key, is_backbone=True).all()
                        if bb_entries:
                            backbone_pins = {}
                            for e in bb_entries:
                                if e.class_session_id not in backbone_pins:
                                    backbone_pins[e.class_session_id] = e.timeslot_id
                            # Backbone takes priority over preserve-mode carry-overs
                            if pinned_slots:
                                pinned_slots.update(backbone_pins)
                            else:
                                pinned_slots = backbone_pins

                        success, message, s = solve(
                            tri_key, start_date, term_break_weeks,
                            trimester_num=tri_num,
                            academic_year=academic_year,
                            pinned_slots=pinned_slots,
                            historical_preferred=historical_preferred,
                        )
                        if success:
                            any_success = True
                            _auto_create_flags(tri_key, s.get('preferred_violations', []))
                        ay_stats[tri_num] = {
                            'success': success,
                            'message': message,
                            'stats'  : s,
                        }
                    except Exception as e:
                        ay_stats[tri_num] = {
                            'success': False,
                            'message': f'Solver error: {str(e)}',
                            'stats'  : {},
                        }

                if any_success:
                    succeeded = [t for t, v in ay_stats.items() if v['success']]
                    flash(
                        f'{academic_year} — Tri {", ".join(str(t) for t in succeeded)} '
                        f'generated successfully.',
                        'success'
                    )
                else:
                    flash('No trimesters were generated. Check issues above.', 'danger')

                # For the tab display, default to T1 after a Full AY run
                if not trimester:
                    trimester = f'{academic_year}-T1'

        elif action == 'generate':
            # New fields: academic_year + trimester_num; build the internal key
            academic_year = request.form.get('academic_year', '').strip().upper()
            tri_raw       = request.form.get('trimester_num', '').strip()
            trimester_num = int(tri_raw) if tri_raw.isdigit() and tri_raw in ('1', '2', '3') else None
            selected_tri  = trimester_num
            if academic_year and trimester_num:
                trimester = f'{academic_year}-T{trimester_num}'

            # Re-check issues scoped to the selected trimester only
            if trimester_num:
                tri_blockers, tri_warns = get_blocking_issues(trimester_num=trimester_num)
            else:
                tri_blockers, tri_warns = issues, issue_warnings

            # Auto-populate start date from official SIT calendar if not provided
            if not start_raw and academic_year and trimester_num:
                start_raw = SIT_ACADEMIC_CALENDAR.get(academic_year, {}).get(trimester_num, '')

            if tri_blockers:
                flash('Resolve all blocking issues for this trimester before generating.', 'danger')
                for iss in tri_blockers[:5]:
                    flash(iss, 'warning')
            elif not academic_year:
                flash('Academic year is required (e.g. AY2526).', 'danger')
            elif not trimester_num:
                flash('Trimester number (1, 2, or 3) is required.', 'danger')
            elif not start_raw:
                flash('Start date is required (AY not in known SIT calendar — enter manually).', 'danger')
            else:
                try:
                    start_date = date.fromisoformat(start_raw)
                    # Parse term break weeks (comma-separated, e.g. "7" or "7,14")
                    break_raw = request.form.get('term_break_weeks', '7').strip()
                    term_break_weeks = set()
                    for part in break_raw.split(','):
                        part = part.strip()
                        if part.isdigit():
                            term_break_weeks.add(int(part))
                    if not term_break_weeks:
                        term_break_weeks = {7}   # safe fallback
                    preserve = request.form.get('preserve_existing') == 'on'
                    pinned_slots = None
                    if preserve:
                        existing = TimetableEntry.query.filter_by(trimester=trimester, is_backbone=False).all()
                        seen_sess = set()
                        pinned_slots = {}
                        for e in existing:
                            if e.class_session_id not in seen_sess:
                                seen_sess.add(e.class_session_id)
                                pinned_slots[e.class_session_id] = e.timeslot_id
                        if not pinned_slots:
                            pinned_slots = None

                    historical_preferred = _build_historical_preferred(academic_year, trimester_num)

                    # Hard-pin backbone sessions so generated == backbone (1:1 similarity).
                    bb_entries = TimetableEntry.query.filter_by(
                        trimester=trimester, is_backbone=True).all()
                    if bb_entries:
                        backbone_pins = {}
                        for e in bb_entries:
                            if e.class_session_id not in backbone_pins:
                                backbone_pins[e.class_session_id] = e.timeslot_id
                        if pinned_slots:
                            pinned_slots.update(backbone_pins)
                        else:
                            pinned_slots = backbone_pins

                    success, message, stats = solve(
                        trimester, start_date, term_break_weeks,
                        trimester_num=trimester_num,
                        academic_year=academic_year,
                        pinned_slots=pinned_slots,
                        historical_preferred=historical_preferred,
                    )
                    result = {'success': success, 'message': message}
                    if success:
                        flash(message, 'success')
                        # Auto-create TimetableFlag records for preferred violations
                        _auto_create_flags(trimester, stats.get('preferred_violations', []))
                    else:
                        flash(message, 'danger')
                except Exception as e:
                    flash(f'Solver error: {str(e)}', 'danger')

        elif action == 'publish':
            TimetableEntry.query.filter_by(trimester=trimester, is_published=False).update(
                {'is_published': True}
            )
            db.session.commit()
            flash(f'Timetable for {trimester} published. Professors and students can now view it.', 'success')

        elif action == 'reset':
            from app.models.academic_calendar import AcademicCalendar
            from app.models.timetable_flag import TimetableFlag
            from app.models.flag_response import FlagResponse

            # Only delete solver-generated (non-backbone) entries
            non_backbone_ids = [
                e.id for e in TimetableEntry.query.filter_by(trimester=trimester, is_backbone=False).all()
            ]
            if non_backbone_ids:
                flag_ids = [
                    f.id for f in TimetableFlag.query
                    .filter(TimetableFlag.timetable_entry_id.in_(non_backbone_ids))
                    .all()
                ]
                if flag_ids:
                    FlagResponse.query.filter(FlagResponse.flag_id.in_(flag_ids)).delete(synchronize_session=False)
                    TimetableFlag.query.filter(TimetableFlag.id.in_(flag_ids)).delete(synchronize_session=False)
                TimetableEntry.query.filter(TimetableEntry.id.in_(non_backbone_ids)).delete(synchronize_session=False)

            backbone_count = TimetableEntry.query.filter_by(trimester=trimester, is_backbone=True).count()
            if backbone_count == 0:
                AcademicCalendar.query.filter_by(trimester=trimester).delete()

            db.session.commit()
            note = f' ({backbone_count} backbone entries preserved)' if backbone_count else ''
            flash(f'Solver entries for {trimester} cleared ({len(non_backbone_ids)} deleted{note}).', 'info')
            trimester = ''

        elif action == 'reset_ay':
            # Clear solver-generated entries for all 3 trimesters (backbone preserved)
            from app.models.academic_calendar import AcademicCalendar
            from app.models.timetable_flag import TimetableFlag
            from app.models.flag_response import FlagResponse
            clear_ay = request.form.get('clear_ay', '').strip()
            if clear_ay:
                tri_keys = [f'{clear_ay}-T1', f'{clear_ay}-T2', f'{clear_ay}-T3']
                non_backbone_ids = [
                    e.id for e in TimetableEntry.query.filter(
                        TimetableEntry.trimester.in_(tri_keys),
                        TimetableEntry.is_backbone == False
                    ).all()
                ]
                if non_backbone_ids:
                    flag_ids = [
                        f.id for f in TimetableFlag.query
                        .filter(TimetableFlag.timetable_entry_id.in_(non_backbone_ids))
                        .all()
                    ]
                    if flag_ids:
                        FlagResponse.query.filter(FlagResponse.flag_id.in_(flag_ids)).delete(synchronize_session=False)
                        TimetableFlag.query.filter(TimetableFlag.id.in_(flag_ids)).delete(synchronize_session=False)
                    TimetableEntry.query.filter(TimetableEntry.id.in_(non_backbone_ids)).delete(synchronize_session=False)
                deleted = len(non_backbone_ids)
                # Only clear calendar if no backbone entries remain for that trimester
                for tk in tri_keys:
                    if TimetableEntry.query.filter_by(trimester=tk, is_backbone=True).count() == 0:
                        AcademicCalendar.query.filter_by(trimester=tk).delete()
                db.session.commit()
                flash(f'All timetables for {clear_ay} cleared ({deleted} entries deleted).', 'info')

    # Load timetable entries for display
    entries = []
    trimesters = [r[0] for r in
                  db.session.query(TimetableEntry.trimester).distinct().order_by(TimetableEntry.trimester).all()]

    existing_ays = sorted(set(
        t[:6] for t in trimesters if t.startswith('AY') and len(t) >= 6
    ))
    ay_options = sorted(set(existing_ays + [ay_default, _next_ay(ay_default)]))

    prog_filter = request.args.get('prog', '')

    # Check whether the active AY has backbone entries (for source toggle)
    _active_ay_pfx = trimester[:6] if trimester and trimester.startswith('AY') and len(trimester) >= 6 else ''
    has_backbone_ay = bool(_active_ay_pfx and db.session.query(TimetableEntry.id).filter(
        TimetableEntry.trimester.startswith(_active_ay_pfx),
        TimetableEntry.is_backbone == True
    ).limit(1).scalar())
    # Default to 'generated' when backbone exists and no explicit source chosen
    if has_backbone_ay and not source:
        source = 'generated'

    if trimester:
        if trimester.endswith('-all'):
            ay_prefix = trimester[:-4]  # 'AY2627'
            q = TimetableEntry.query.filter(TimetableEntry.trimester.startswith(ay_prefix))
            if source == 'bb':
                q = q.filter(TimetableEntry.is_backbone == True)
            elif source == 'generated':
                q = q.filter(TimetableEntry.is_backbone == False)
            entries = (q.join(TimetableEntry.timeslot)
                       .order_by(TimetableEntry.trimester, TimeSlot.day_of_week, TimeSlot.period_label)
                       .all())
        else:
            q = TimetableEntry.query.filter_by(trimester=trimester)
            if source == 'bb':
                q = q.filter(TimetableEntry.is_backbone == True)
            elif source == 'generated':
                q = q.filter(TimetableEntry.is_backbone == False)
            entries = (q.join(TimetableEntry.timeslot)
                       .order_by(TimeSlot.day_of_week, TimeSlot.period_label)
                       .all())

    # Programmes present in current view (for active highlighting)
    active_prog_codes = set(
        e.class_session.course.programme.code
        for e in entries
        if e.class_session.course.programme
    )
    # All programmes that have any timetable entry ever (always shown in filter)
    from app.models.programme import Programme
    all_programmes = sorted(
        set(
            (e[0], e[1]) for e in
            db.session.query(Programme.code, Programme.name)
            .join(Programme.courses)
            .join(Course.class_sessions)
            .join(ClassSession.timetable_entries)
            .distinct()
            .all()
        ),
        key=lambda x: x[0]
    )
    programmes = all_programmes  # kept for template compatibility

    # Apply programme filter
    if prog_filter:
        entries = [e for e in entries
                   if e.class_session.course.programme
                   and e.class_session.course.programme.code == prog_filter]

    # Deduplicate: show one row per session (not one per week)
    seen = set()
    unique_entries = []
    for e in entries:
        if e.class_session_id not in seen:
            seen.add(e.class_session_id)
            unique_entries.append(e)

    day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    if trimester.endswith('-all'):
        unique_entries.sort(key=lambda e: (
            e.trimester,
            day_order.index(e.timeslot.day_of_week),
            e.timeslot.start_time
        ))
    else:
        unique_entries.sort(key=lambda e: (
            day_order.index(e.timeslot.day_of_week),
            e.timeslot.start_time
        ))

    # ---------------------------------------------------------------------------
    # Weekly view support
    # ---------------------------------------------------------------------------
    from app.models.academic_calendar import AcademicCalendar

    DAYS_ALL = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    view_mode = 'list' if trimester.endswith('-all') else request.args.get('view', 'list')
    week_number     = request.args.get('week', 1, type=int)
    calendar_weeks  = []
    period_slots    = []
    week_grid       = {d: {} for d in DAYS_ALL}
    current_cal_week = None
    prev_week_num   = None
    next_week_num   = None

    if trimester:
        calendar_weeks = (AcademicCalendar.query
                          .filter_by(trimester=trimester)
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

        wq = (TimetableEntry.query
              .join(TimetableEntry.class_session)
              .join(TimetableEntry.timeslot)
              .filter(
                  TimetableEntry.trimester == trimester,
                  TimetableEntry.week_number == week_number,
              ))
        if source == 'bb':
            wq = wq.filter(TimetableEntry.is_backbone == True)
        elif source == 'generated':
            wq = wq.filter(TimetableEntry.is_backbone == False)
        week_entries_raw = wq.all()

        if prog_filter:
            week_entries_raw = [e for e in week_entries_raw
                                if e.class_session.course.programme
                                and e.class_session.course.programme.code == prog_filter]

        # Store a list per cell so multiple sessions in the same slot all appear
        week_grid = {day: {ts.period_label: [] for ts in period_slots} for day in DAYS_ALL}
        for entry in week_entries_raw:
            d = entry.timeslot.day_of_week
            p = entry.timeslot.period_label
            if d in week_grid and p in week_grid[d]:
                week_grid[d][p].append(entry)

    # Year levels available in this trimester (for filter buttons)
    year_levels = sorted(set(
        e.class_session.course.year_level
        for e in unique_entries
        if e.class_session.course.year_level
    )) if unique_entries else []

    return render_template('admin/timetable.html',
                           issues=issues,
                           issue_warnings=issue_warnings,
                           trimesters=trimesters,
                           active_trimester=trimester,
                           entries=unique_entries,
                           stats=stats,
                           ay_stats=ay_stats,
                           result=result,
                           view_mode=view_mode,
                           week_number=week_number,
                           calendar_weeks=calendar_weeks,
                           current_cal_week=current_cal_week,
                           prev_week_num=prev_week_num,
                           next_week_num=next_week_num,
                           period_slots=period_slots,
                           week_grid=week_grid,
                           days_all=DAYS_ALL,
                           year_levels=year_levels,
                           programmes=programmes,
                           active_prog_codes=active_prog_codes,
                           prog_filter=prog_filter,
                           ay_default=ay_default,
                           ay_options=ay_options,
                           selected_tri=selected_tri,
                           sit_calendar=SIT_ACADEMIC_CALENDAR,
                           source=source,
                           has_backbone_ay=has_backbone_ay)


# ---------------------------------------------------------------------------
# Timetable Similarity — mirrored timetable report across trimesters
# ---------------------------------------------------------------------------

@admin_bp.route('/timetable/similarity')
@login_required
def timetable_similarity():
    """Cross-AY comparison: did the historical soft constraint keep slots consistent?"""
    # Available AYs from timetable entries
    all_ays = sorted(set(
        e[0] for e in db.session.query(TimetableEntry.academic_year).distinct().all()
        if e[0]
    ))

    def _slot_label(ts):
        return f'{ts.day_of_week[:3]} {ts.start_time.strftime("%H:%M")}–{ts.end_time.strftime("%H:%M")}'

    # Build combined option list: value='AY2526:backbone', label='AY2526BB'
    # Backbone variant uses BB suffix; generated variant uses plain AY name.
    similarity_options = []  # list of (value, label)
    for ay in all_ays:
        has_bb  = TimetableEntry.query.filter_by(academic_year=ay, is_backbone=True).first()  is not None
        has_gen = TimetableEntry.query.filter_by(academic_year=ay, is_backbone=False).first() is not None
        if has_bb:
            similarity_options.append((f'{ay}:backbone',  f'{ay}BB'))
        if has_gen:
            similarity_options.append((f'{ay}:generated', ay))
        if not has_bb and not has_gen:
            similarity_options.append((f'{ay}:all', ay))

    def _parse_opt(val):
        """Split 'AY2526:backbone' → (ay, source). Default source = 'all'."""
        if val and ':' in val:
            ay, src = val.split(':', 1)
            return ay, src
        return val or '', 'all'

    # Smart defaults: base → first backbone option, compare → first generated option
    def _default_opt(prefer_backbone):
        for v, _ in similarity_options:
            ay, src = _parse_opt(v)
            if prefer_backbone and src == 'backbone':
                return v
        for v, _ in similarity_options:
            ay, src = _parse_opt(v)
            if not prefer_backbone and src == 'generated':
                return v
        return similarity_options[0][0] if similarity_options else ''

    default_base    = _default_opt(True)
    default_compare = _default_opt(False)
    # If both defaults resolve to the same option, push compare to the next distinct one
    if default_base == default_compare and len(similarity_options) >= 2:
        default_compare = similarity_options[1][0]

    base_val    = request.args.get('base',    default_base)
    compare_val = request.args.get('compare', default_compare)
    base_ay,    base_source    = _parse_opt(base_val)
    compare_ay, compare_source = _parse_opt(compare_val)

    # Human-readable labels for headings/alerts
    _opt_labels = dict(similarity_options)
    base_label    = _opt_labels.get(base_val,    base_ay)
    compare_label = _opt_labels.get(compare_val, compare_ay)

    cross_rows = []

    if base_ay and compare_ay:
        def _build_map(trimester_key, source='all'):
            q = TimetableEntry.query.filter_by(trimester=trimester_key)
            if source == 'backbone':
                q = q.filter_by(is_backbone=True)
            elif source == 'generated':
                q = q.filter_by(is_backbone=False)
            entries = q.all()
            slot_map = {}
            for e in entries:
                key = (e.class_session.course.module_code, e.class_session.session_type)
                if key not in slot_map:
                    ts = e.timeslot
                    slot_map[key] = {
                        'label':            _slot_label(ts),
                        'day':              ts.day_of_week,
                        'start':            ts.start_time.strftime('%H:%M'),
                        'room':             e.room.room_code if e.room else None,
                        'timeslot_id':      ts.id,
                        'room_id':          e.room_id,
                        'class_session_id': e.class_session_id,
                    }
            return slot_map

        def _reason_tag(class_session_id, base_timeslot_id, base_room_id, compare_tri_key):
            """Infer why the solver moved a session off its historical slot.
            Returns (tag, explanation) — explanation is a specific, data-driven sentence for the admin."""
            cs = ClassSession.query.get(class_session_id)
            base_ts = TimeSlot.query.get(base_timeslot_id)
            slot_str = (f"{base_ts.day_of_week} {base_ts.start_time.strftime('%H:%M')}–{base_ts.end_time.strftime('%H:%M')}"
                        if base_ts else "the backbone slot")
            if cs:
                for prof in cs.all_professors:
                    hard = AvailabilityDeclaration.query.filter_by(
                        professor_id=prof.id,
                        timeslot_id=base_timeslot_id,
                        constraint_type='strict',
                    ).first()
                    if hard:
                        return ('prof_hard_conflict',
                                f"Prof. {prof.user.name} has a strict unavailability at {slot_str} — the solver cannot override this hard constraint.")
                    soft = AvailabilityDeclaration.query.filter_by(
                        professor_id=prof.id,
                        timeslot_id=base_timeslot_id,
                    ).first()
                    if soft:
                        return ('prof_unavailable',
                                f"Prof. {prof.user.name} is unavailable at {slot_str}, so the solver found a different slot.")
                if cs.student_group_id:
                    group_clash = (TimetableEntry.query
                                   .join(TimetableEntry.class_session)
                                   .filter(
                                       TimetableEntry.trimester == compare_tri_key,
                                       TimetableEntry.timeslot_id == base_timeslot_id,
                                       ClassSession.student_group_id == cs.student_group_id,
                                       TimetableEntry.class_session_id != class_session_id,
                                   ).first())
                    if group_clash:
                        clash_cs = group_clash.class_session
                        sg = cs.student_group.group_label if cs.student_group else 'the same group'
                        return ('group_overlap',
                                f"Group {sg} already has {clash_cs.course.module_code} {clash_cs.session_type} at {slot_str}.")
            if base_room_id:
                room_clash = TimetableEntry.query.filter_by(
                    trimester=compare_tri_key,
                    room_id=base_room_id,
                    timeslot_id=base_timeslot_id,
                ).first()
                if room_clash:
                    rc_cs = room_clash.class_session
                    base_room = Room.query.get(base_room_id)
                    room_code = base_room.room_code if base_room else 'the backbone room'
                    return ('room_conflict',
                            f"{room_code} is occupied by {rc_cs.course.module_code} {rc_cs.session_type} at {slot_str}.")
            return ('rescheduled',
                    'No specific constraint identified — the solver chose a different slot through optimisation.')

        # Module codes that existed in the base AY (filtered by source) — used to
        # distinguish truly-new modules from ones that simply had no backbone entry
        base_ay_mods_q = TimetableEntry.query.filter(TimetableEntry.academic_year == base_ay)
        if base_source == 'backbone':
            base_ay_mods_q = base_ay_mods_q.filter_by(is_backbone=True)
        elif base_source == 'generated':
            base_ay_mods_q = base_ay_mods_q.filter_by(is_backbone=False)
        base_ay_mods = set(
            e.class_session.course.module_code.upper() for e in base_ay_mods_q.all()
        )
        # Also include all courses that exist in the system (curriculum-level check)
        all_curriculum_mods = set(c.module_code.upper() for c in Course.query.all())

        for tri_num in [1, 2, 3]:
            base_map    = _build_map(f'{base_ay}-T{tri_num}',    base_source)
            compare_map = _build_map(f'{compare_ay}-T{tri_num}', compare_source)
            if not base_map and not compare_map:
                continue

            all_keys = sorted(set(base_map.keys()) | set(compare_map.keys()))
            for mod, stype in all_keys:
                bd = base_map.get((mod, stype))
                cd = compare_map.get((mod, stype))
                base_slot    = bd['label'] if bd else None
                compare_slot = cd['label'] if cd else None

                if base_slot and compare_slot:
                    consistency = 'same' if base_slot == compare_slot else 'different'
                elif base_slot:
                    consistency = 'base_only'
                else:
                    consistency = 'compare_only'

                reason     = ''
                reason_tag = ''
                # A module is only "truly new" if it doesn't exist in the curriculum at all.
                # If it exists in the DB but is missing from the backbone, that's a backbone
                # import gap — not a new module.
                is_truly_new = (mod.upper() not in all_curriculum_mods
                                and mod.upper() not in base_ay_mods)
                is_backbone_gap = (mod.upper() in all_curriculum_mods
                                   and mod.upper() not in base_ay_mods)

                if consistency == 'different':
                    parts = []
                    if bd['day'] != cd['day']:
                        parts.append(f"Day: {bd['day'][:3]} → {cd['day'][:3]}")
                    if bd['start'] != cd['start']:
                        parts.append(f"Time: {bd['start']} → {cd['start']}")
                    if bd['room'] != cd['room']:
                        parts.append(f"Room: {bd['room'] or '?'} → {cd['room'] or '?'}")
                    reason = ' | '.join(parts) if parts else 'Slot changed'
                    reason_tag, explanation = _reason_tag(
                        bd['class_session_id'],
                        bd['timeslot_id'],
                        bd['room_id'],
                        f'{compare_ay}-T{tri_num}',
                    )
                elif consistency == 'base_only':
                    reason = f'Not scheduled in {compare_ay}'
                    explanation = f'Present in {base_label} but not scheduled in {compare_label}. The module may have been dropped or excluded from the new run.'
                elif consistency == 'compare_only':
                    if is_truly_new:
                        reason = f'New module added in {compare_ay}'
                        explanation = f'Not in {base_label} at all — genuinely new to the curriculum, scheduled without a historical reference.'
                    else:
                        reason = f'Missing from {base_label} (backbone import gap)'
                        explanation = f'Module exists in the curriculum but was not captured in the {base_label} import. It was scheduled freely in {compare_label} with no historical slot to follow.'
                else:  # same
                    explanation = ''

                cross_rows.append({
                    'tri_num'         : tri_num,
                    'module_code'     : mod,
                    'session_type'    : stype,
                    'base_slot'       : base_slot,
                    'compare_slot'    : compare_slot,
                    'consistency'     : consistency,
                    'reason'          : reason,
                    'reason_tag'      : reason_tag,
                    'is_truly_new'    : is_truly_new,
                    'is_backbone_gap' : is_backbone_gap,
                    'explanation'     : explanation,
                })

    same_count = sum(1 for r in cross_rows if r['consistency'] == 'same')
    diff_count = sum(1 for r in cross_rows if r['consistency'] == 'different')

    # Per-trimester breakdown
    tri_stats = {}
    for tri_num in [1, 2, 3]:
        s = sum(1 for r in cross_rows if r['tri_num'] == tri_num and r['consistency'] == 'same')
        d = sum(1 for r in cross_rows if r['tri_num'] == tri_num and r['consistency'] == 'different')
        total = s + d
        tri_stats[tri_num] = {
            'same':  s,
            'diff':  d,
            'total': total,
            'pct':   round(s / total * 100) if total > 0 else None,
        }

    return render_template('admin/timetable_similarity.html',
                           similarity_options=similarity_options,
                           base_val=base_val,
                           compare_val=compare_val,
                           base_ay=base_ay,
                           compare_ay=compare_ay,
                           base_label=base_label,
                           compare_label=compare_label,
                           cross_rows=cross_rows,
                           same_count=same_count,
                           diff_count=diff_count,
                           tri_stats=tri_stats)


# ---------------------------------------------------------------------------
# Timetable export — flat XLSX download
# ---------------------------------------------------------------------------

@admin_bp.route('/timetable/export')
@login_required
def timetable_export():
    import io
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from flask import send_file

    trimester_filter = request.args.get('trimester', '')

    q = TimetableEntry.query
    if trimester_filter:
        q = q.filter_by(trimester=trimester_filter)
    entries = q.order_by(
        TimetableEntry.trimester,
        TimetableEntry.week_number,
    ).all()

    # Session-type colour palette (hex fill colours)
    TYPE_COLOURS = {
        'lecture':  'D6E4F0',
        'lectorial':'D6E4F0',
        'tutorial': 'D5F5E3',
        'lab':      'FEF9E7',
        'seminar':  'F9EBEA',
    }
    DEFAULT_COLOUR = 'F2F3F4'

    HDR_FILL = PatternFill('solid', fgColor='2E4057')
    HDR_FONT = Font(bold=True, color='FFFFFF', size=10)
    BOLD     = Font(bold=True, size=9)
    SMALL    = Font(size=9)
    CENTRE   = Alignment(horizontal='center', vertical='center', wrap_text=True)
    LEFT     = Alignment(horizontal='left',   vertical='top',    wrap_text=True)
    THIN     = Side(style='thin', color='BBBBBB')
    BORDER   = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

    DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']

    def _make_sheet(wb, title, sheet_entries):
        """Build one grid sheet: rows=weeks, columns=Day×Period."""
        from app.models.academic_calendar import AcademicCalendar

        # Gather unique time slots from the entries (ordered)
        from app.models.timeslot import TimeSlot as TS
        slot_ids = sorted(set(e.timeslot_id for e in sheet_entries),
                          key=lambda sid: (
                              DAYS.index(next(e.timeslot.day_of_week for e in sheet_entries if e.timeslot_id == sid)),
                              next(e.timeslot.start_time for e in sheet_entries if e.timeslot_id == sid)
                          ))
        # Build ordered list of (day, start, end, period_label) tuples
        col_slots = []
        seen_cols = set()
        for e in sorted(sheet_entries, key=lambda x: (
                DAYS.index(x.timeslot.day_of_week) if x.timeslot.day_of_week in DAYS else 9,
                x.timeslot.start_time)):
            ts = e.timeslot
            key = (ts.day_of_week, ts.start_time, ts.end_time)
            if key not in seen_cols:
                seen_cols.add(key)
                col_slots.append((ts.day_of_week, ts.start_time, ts.end_time, ts.period_label))

        if not col_slots:
            return

        # Gather weeks
        weeks = sorted(set(e.week_number for e in sheet_entries))
        trimester = sheet_entries[0].trimester if sheet_entries else ''
        cal_map = {
            cw.week_number: cw
            for cw in AcademicCalendar.query.filter_by(trimester=trimester).all()
        } if trimester else {}

        ws = wb.create_sheet(title=title)

        # Header row 1: Day spans
        ws.cell(1, 1, 'Week').font = HDR_FONT
        ws.cell(1, 1).fill = HDR_FILL
        ws.cell(1, 1).alignment = CENTRE
        ws.cell(2, 1, 'Date').font = HDR_FONT
        ws.cell(2, 1).fill = HDR_FILL
        ws.cell(2, 1).alignment = CENTRE

        col = 2
        day_start_col = {}
        for day, start, end, period in col_slots:
            if day not in day_start_col:
                day_start_col[day] = col
            label = f'{start.strftime("%H:%M")}–{end.strftime("%H:%M")}'
            c = ws.cell(1, col, day if col == day_start_col[day] else '')
            c.font = HDR_FONT
            c.fill = HDR_FILL
            c.alignment = CENTRE
            c2 = ws.cell(2, col, label)
            c2.font = HDR_FONT
            c2.fill = HDR_FILL
            c2.alignment = CENTRE
            ws.column_dimensions[get_column_letter(col)].width = 22
            col += 1

        ws.column_dimensions['A'].width = 12

        # Merge day header cells
        col = 2
        for day in DAYS:
            day_cols = [i + 2 for i, (d, _, _, _) in enumerate(col_slots) if d == day]
            if len(day_cols) > 1:
                ws.merge_cells(start_row=1, start_column=day_cols[0],
                               end_row=1, end_column=day_cols[-1])
            col += len(day_cols)

        # Build lookup: (week, day, start_time) -> entry list
        from collections import defaultdict
        cell_map = defaultdict(list)
        for e in sheet_entries:
            cell_map[(e.week_number, e.timeslot.day_of_week, e.timeslot.start_time)].append(e)

        # Data rows
        data_row = 3
        for wk in weeks:
            cw = cal_map.get(wk)
            week_label = f'Week {wk}'
            if cw and cw.start_date:
                date_label = cw.start_date.strftime('%d %b')
            else:
                date_label = ''

            wc = ws.cell(data_row, 1, week_label)
            wc.font = BOLD
            wc.alignment = CENTRE
            wc.border = BORDER
            if cw and cw.is_term_break:
                wc.fill = PatternFill('solid', fgColor='FFF3CD')

            dc = ws.cell(data_row + 1, 1, date_label)
            dc.font = SMALL
            dc.alignment = CENTRE
            dc.border = BORDER

            col = 2
            for day, start, end, period in col_slots:
                cell_entries = cell_map.get((wk, day, start), [])
                if cell_entries:
                    e = cell_entries[0]
                    cs  = e.class_session
                    crs = cs.course
                    prof = (e.override_professor.user.name if e.override_professor
                            else (cs.primary_professor.user.name if cs.primary_professor else ''))
                    room = e.room.room_code if e.room else 'Online'
                    text = f'{crs.module_code}\n{cs.session_type.capitalize()}\n{room}'
                    if prof:
                        text += f'\n{prof}'
                    colour = TYPE_COLOURS.get(cs.session_type, DEFAULT_COLOUR)
                    fill = PatternFill('solid', fgColor=colour)
                    c = ws.cell(data_row, col, text)
                    c.fill = fill
                    c.font = SMALL
                    c.alignment = LEFT
                    c.border = BORDER
                    ws.row_dimensions[data_row].height = 52
                else:
                    c = ws.cell(data_row, col, '')
                    c.border = BORDER
                col += 1

            # Second sub-row for date label already written; border remaining cells
            for col2 in range(2, col):
                ws.cell(data_row + 1, col2).border = BORDER

            data_row += 2

        ws.freeze_panes = 'B3'

    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # remove default sheet

    # Group entries by trimester then year level
    from collections import defaultdict
    tri_year_map = defaultdict(lambda: defaultdict(list))
    for e in entries:
        yr = e.class_session.course.year_level or 0
        tri_year_map[e.trimester][yr].append(e)

    for tri_key in sorted(tri_year_map.keys()):
        yr_map = tri_year_map[tri_key]
        for yr in sorted(yr_map.keys()):
            sheet_entries = yr_map[yr]
            yr_label = f'Y{yr}' if yr else 'All'
            short_tri = tri_key.replace('AY', '').replace('-T', ' T')
            sheet_title = f'{short_tri} {yr_label}'[:31]  # Excel limit
            _make_sheet(wb, sheet_title, sheet_entries)

    if not wb.sheetnames:
        ws = wb.create_sheet('No data')
        ws['A1'] = 'No timetable entries found.'

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f'timetable_{trimester_filter or "all"}.xlsx'
    return send_file(buf, download_name=filename,
                     as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


# ---------------------------------------------------------------------------
# Template 2 export — SIT upload format (flat row-per-session-pattern)
# ---------------------------------------------------------------------------

@admin_bp.route('/timetable/export-template2')
@login_required
def timetable_export_template2():
    import io, re
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    from flask import send_file
    from collections import defaultdict
    from datetime import time as dtime

    trimester_filter = request.args.get('trimester', '')

    CLASS_TYPE = {
        'lecture': 'Lecture', 'lectorial': 'Lectorial', 'tutorial': 'Tutorial',
        'lab': 'Lab', 'seminar': 'Seminar', 'workshop': 'Workshop', 'quiz': 'Quiz',
    }
    ACT_CODE = {
        'lecture': 'LET', 'lectorial': 'LET', 'tutorial': 'TUT',
        'lab': 'LAB', 'seminar': 'SEM', 'workshop': 'WRK', 'quiz': 'QUZ',
    }
    DAY_ABBR = {
        'Monday': 'Mon', 'Tuesday': 'Tue', 'Wednesday': 'Wed',
        'Thursday': 'Thu', 'Friday': 'Fri',
    }
    CLUSTER_ABBR = {
        'ENG': 'ENG', 'Engineering': 'ENG', 'ICT': 'ICT',
        'University-Wide': 'UWM', 'Business': 'BUS', 'Health': 'HLS',
    }
    PROG_SECTOR = {
        'ASE': ('DOVER', 'DV'), 'CVE': ('DOVER', 'DV'), 'SDE': ('DOVER', 'DV'),
        'NAME': ('DOVER', 'DV'), 'RSE': ('TP', 'TP'), 'EDE': ('SP', 'SP'),
        'EEE': ('NYP', 'NY'), 'EPE': ('NYP', 'NY'), 'METS': ('NYP', 'NY'),
        'MEC': ('NP', 'NP'), 'MDME': ('NP', 'NP'), 'SBE': ('PUNGGOL', 'PU'),
        'ESE': ('PUNGGOL', 'PU'), 'DSC': ('DOVER', 'DV'), 'CPC': ('DOVER', 'DV'),
        'ISE': ('NYP', 'NY'),
    }
    HEADERS = [
        'Module', 'Class Type', 'Template', 'Group', 'Day', 'Start', 'End',
        'Class Size', 'Sector', 'RoomGrouping', 'Room1', 'Room2', 'StaffGrouping',
        'Staff1', 'Staff2', 'Tri Week', 'Recording Mode', 'Remark',
        'FMTS Tri Start Week', 'Activity Hostkey', 'SIS Module Code', 'Term',
        'Activity Type', 'Duration', 'Staff Suitability ID', 'SIS Staff ID',
        'SIS Staff ID 2', 'Zone Hoskey', 'Location Suitability ID',
        'Location Hostkey', 'Location Hostkey 2',
    ]
    COL_WIDTHS = {
        'Module': 12, 'Class Type': 12, 'Template': 9, 'Group': 8, 'Day': 6,
        'Start': 7, 'End': 7, 'Class Size': 10, 'Sector': 10, 'Staff1': 28,
        'Staff2': 28, 'Tri Week': 22, 'Activity Hostkey': 42,
        'SIS Module Code': 32, 'Term': 7, 'Activity Type': 13, 'Duration': 9,
        'SIS Staff ID': 14, 'SIS Staff ID 2': 14, 'Zone Hoskey': 12,
    }

    def _term_code(tri_str):
        m = re.match(r'AY(\d{2})\d{2}-T(\d)', tri_str or '')
        return f'{m.group(1)}{m.group(2)}0' if m else '2510'

    # Resolve trimester int from string 'AY2526-T1' → 1
    tri_int = None
    if trimester_filter:
        m = re.match(r'AY\d{4}-T(\d)', trimester_filter)
        if m:
            tri_int = int(m.group(1))

    # Load sessions with all relationships eager-loaded
    q = (
        ClassSession.query
        .join(ClassSession.course)
        .options(
            db.joinedload(ClassSession.course).joinedload(Course.programme),
            db.joinedload(ClassSession.professor_assignments)
                .joinedload(ClassSessionProfessor.professor)
                .joinedload(Professor.user),
            db.joinedload(ClassSession.student_group),
            db.joinedload(ClassSession.timetable_entries)
                .joinedload(TimetableEntry.timeslot),
        )
    )
    if tri_int is not None:
        q = q.filter(ClassSession.trimester == tri_int)
    all_sessions = q.order_by(Course.module_code, ClassSession.session_type).all()

    # Build slot map: cs.id → (timeslot | None, sorted_weeks_list)
    slot_map = {}
    for cs in all_sessions:
        te_list = [e for e in cs.timetable_entries
                   if not trimester_filter or e.trimester == trimester_filter]
        if te_list:
            ts = te_list[0].timeslot
            weeks = sorted(set(e.week_number for e in te_list if e.week_number != 7))
            slot_map[cs.id] = (ts, weeks)
        elif cs.fixed_timeslot_id and cs.fixed_timeslot:
            weeks = ([int(w) for w in cs.teaching_weeks.split(',') if w.strip()]
                     if cs.teaching_weeks else [])
            slot_map[cs.id] = (cs.fixed_timeslot, weeks)
        else:
            slot_map[cs.id] = (None, [])

    # Sort for stable Template numbering
    DAYS_ORD = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4}

    def _sort_key(cs):
        ts, _ = slot_map[cs.id]
        d = DAYS_ORD.get(ts.day_of_week, 9) if ts else 9
        s = ts.start_time if ts else dtime(0, 0)
        return (cs.course.module_code, cs.session_type,
                cs.teaching_weeks or '', d, s, cs.group_label or 'All')

    all_sessions.sort(key=_sort_key)

    # Assign Template numbers and Activity Hostkey suffixes
    tmpl_state = defaultdict(lambda: {'patterns': {}, 'counter': 0})
    act_state   = {}   # (mod_key, group, weeks_str) → activity sequential number
    act_counter = defaultdict(int)

    term_code = _term_code(trimester_filter)
    rows = []

    for cs in all_sessions:
        ts, weeks = slot_map[cs.id]
        prog = cs.course.programme
        sector, campus_abbr = PROG_SECTOR.get(prog.code, ('DOVER', 'DV'))
        cluster_abbr = CLUSTER_ABBR.get(prog.cluster, prog.cluster[:3].upper())
        mod_code = cs.course.module_code
        group = cs.group_label or 'All'
        weeks_str = cs.teaching_weeks or (','.join(str(w) for w in weeks) if weeks else '')

        mod_key = (mod_code, cs.session_type)

        # Template number: each unique (weeks_str, timeslot) within mod_key = new template.
        # Unscheduled sessions use cs.id as tiebreaker so parallel unscheduled sessions
        # (e.g. two lectures per week not yet solved) each get their own template number.
        if ts:
            slot_pat = (weeks_str, ts.day_of_week, ts.start_time)
        else:
            slot_pat = (weeks_str, None, cs.id)
        tmpl_s = tmpl_state[mod_key]
        if slot_pat not in tmpl_s['patterns']:
            tmpl_s['counter'] += 1
            tmpl_s['patterns'][slot_pat] = tmpl_s['counter']
        tmpl_num = tmpl_s['patterns'][slot_pat]

        # Activity number (for hostkey suffix): unique per (mod_key, group, weeks_str)
        act_tuple = (mod_key, group, weeks_str)
        if act_tuple not in act_state:
            act_counter[mod_key] += 1
            act_state[act_tuple] = act_counter[mod_key]
        act_num = act_state[act_tuple]
        act_code = ACT_CODE.get(cs.session_type, 'OTH')
        act_sfx  = '' if act_num == 1 else str(act_num)

        hostkey     = f'{mod_code}-{term_code}-{cluster_abbr}-UGRD-{campus_abbr}-{act_code}{act_sfx}/{group}'
        sis_mod     = f'{mod_code}-{term_code}-{cluster_abbr}-UGRD-{campus_abbr}'

        # Staff
        profs = cs.all_professors
        staff1_name = profs[0].user.name if profs else ''
        staff1_id   = profs[0].staff_id  if profs else ''
        staff2_name = profs[1].user.name if len(profs) > 1 else ''
        staff2_id   = profs[1].staff_id  if len(profs) > 1 else ''

        # Timeslot
        if ts:
            day_str   = DAY_ABBR.get(ts.day_of_week, ts.day_of_week[:3])
            start_str = ts.start_time.strftime('%H%M')
            end_str   = ts.end_time.strftime('%H%M')
        else:
            day_str = start_str = end_str = ''

        rows.append({
            'Module':                 mod_code,
            'Class Type':             CLASS_TYPE.get(cs.session_type, cs.session_type.capitalize()),
            'Template':               tmpl_num,
            'Group':                  group,
            'Day':                    day_str,
            'Start':                  start_str,
            'End':                    end_str,
            'Class Size':             cs.student_group.intake_size if cs.student_group else '',
            'Sector':                 sector,
            'RoomGrouping':           '',
            'Room1':                  '',
            'Room2':                  '',
            'StaffGrouping':          '',
            'Staff1':                 staff1_name,
            'Staff2':                 staff2_name,
            'Tri Week':               weeks_str,
            'Recording Mode':         'A0' if cs.session_type == 'lectorial' else '',
            'Remark':                 '',
            'FMTS Tri Start Week':    1,
            'Activity Hostkey':       hostkey,
            'SIS Module Code':        sis_mod,
            'Term':                   term_code,
            'Activity Type':          act_code,
            'Duration':               cs.duration_hours * 3,
            'Staff Suitability ID':   '',
            'SIS Staff ID':           staff1_id,
            'SIS Staff ID 2':         staff2_id,
            'Zone Hoskey':            sector,
            'Location Suitability ID': '',
            'Location Hostkey':       '',
            'Location Hostkey 2':     '',
        })

    # Build Excel workbook
    wb  = openpyxl.Workbook()
    ws  = wb.active
    ws.title = 'Timetable'

    HDR_FILL = PatternFill('solid', fgColor='2E4057')
    HDR_FONT = Font(bold=True, color='FFFFFF', size=10)
    DATA_FONT = Font(size=9)

    for ci, h in enumerate(HEADERS, 1):
        c = ws.cell(1, ci, h)
        c.font = HDR_FONT
        c.fill = HDR_FILL
        ws.column_dimensions[get_column_letter(ci)].width = COL_WIDTHS.get(h, 14)

    for ri, row in enumerate(rows, 2):
        for ci, h in enumerate(HEADERS, 1):
            c = ws.cell(ri, ci, row.get(h, ''))
            c.font = DATA_FONT

    ws.freeze_panes = 'A2'
    if not rows:
        ws.cell(2, 1, 'No sessions found for this trimester.')

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f'template2_{trimester_filter or "all"}.xlsx'
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


# ---------------------------------------------------------------------------
# Timetable summary — plain-English overview via LLM
# ---------------------------------------------------------------------------

@admin_bp.route('/timetable/summary')
@login_required
def timetable_summary():
    from flask import jsonify
    import anthropic as _anthropic

    trimester = request.args.get('trimester', '')
    if not trimester:
        return jsonify({'error': 'No trimester specified'}), 400

    entries = TimetableEntry.query.filter_by(trimester=trimester).all()
    if not entries:
        return jsonify({'error': 'No timetable entries found for this trimester'}), 404

    # Build stats for the prompt
    total_sessions   = len(set(e.class_session_id for e in entries))
    total_weeks      = len(set(e.week_number for e in entries))
    rooms_used       = sorted(set(e.room.room_code for e in entries if e.room))
    module_codes     = sorted(set(e.class_session.course.module_code for e in entries))
    session_types    = {}
    for e in entries:
        st = e.class_session.session_type
        session_types[st] = session_types.get(st, 0) + 1
    type_summary = ', '.join(f'{v} {k}s' for k, v in sorted(session_types.items()))
    profs_set = set()
    for e in entries:
        for p in e.class_session.all_professors:
            profs_set.add(p.name)

    stats_text = (
        f"Trimester: {trimester}\n"
        f"Modules scheduled: {len(module_codes)} ({', '.join(module_codes)})\n"
        f"Unique class sessions: {total_sessions}\n"
        f"Session type breakdown: {type_summary}\n"
        f"Weeks covered: {total_weeks}\n"
        f"Rooms utilised: {len(rooms_used)} ({', '.join(rooms_used[:10])}{'...' if len(rooms_used) > 10 else ''})\n"
        f"Professors teaching: {len(profs_set)}\n"
    )

    api_key = current_app.config.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return jsonify({'error': 'ANTHROPIC_API_KEY not configured in config.py'}), 500

    client = _anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=300,
        messages=[{
            'role': 'user',
            'content': (
                'You are a timetabling administrator writing a brief internal summary of a generated academic timetable. '
                'Write 3–4 concise sentences in plain English. Do not use bullet points. '
                'Do not mention AI, machine learning, algorithms, or any automated system. '
                'Just describe what was scheduled factually, as a human administrator would write it.\n\n'
                f'Timetable statistics:\n{stats_text}'
            ),
        }],
    )
    summary = msg.content[0].text.strip()
    return jsonify({'summary': summary})


# ---------------------------------------------------------------------------
# Events — planned events as hard constraints
# ---------------------------------------------------------------------------

@admin_bp.route('/events')
@login_required
def events():
    from app.models.event import Event
    from app.models.timetable_entry import TimetableEntry
    from datetime import date

    all_events = Event.query.order_by(Event.event_date).all()

    # For each event, count how many published timetable entries are affected
    impact = {}
    for ev in all_events:
        affected = (TimetableEntry.query
                    .join(TimetableEntry.class_session)
                    .join(TimetableEntry.timeslot)
                    .filter(TimetableEntry.is_published == True)
                    .all())
        count = 0
        for entry in affected:
            session_date = entry.class_session  # placeholder — detailed check in template
            count_check = _event_affects_entry(ev, entry)
            if count_check:
                count += 1
        impact[ev.id] = count

    return render_template('admin/events.html', events=all_events, impact=impact)


def _event_affects_entry(event, entry):
    """Return True if an event blocks a specific timetable entry."""
    from datetime import timedelta
    _day_offset = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4}
    cal = entry.academic_calendar_week if hasattr(entry, 'academic_calendar_week') else None

    # We need the entry's actual date — requires the calendar week
    from app.models.academic_calendar import AcademicCalendar
    cal_week = AcademicCalendar.query.filter_by(
        trimester=entry.trimester,
        week_number=entry.week_number
    ).first()
    if not cal_week:
        return False

    day_offset = _day_offset.get(entry.timeslot.day_of_week, 0)
    entry_date = cal_week.start_date + timedelta(days=day_offset)

    if entry_date != event.event_date:
        return False

    if event.is_full_day:
        return True

    # Check specific timeslots
    blocked = event.blocked_timeslot_ids
    return entry.timeslot_id in blocked


@admin_bp.route('/events/add', methods=['GET', 'POST'])
@login_required
def event_add():
    from app.models.event import Event
    programmes = Programme.query.order_by(Programme.code).all()
    timeslots  = TimeSlot.query.order_by(TimeSlot.day_of_week, TimeSlot.start_time).all()

    if request.method == 'POST':
        name         = request.form.get('name', '').strip()
        description  = request.form.get('description', '').strip()
        event_date   = request.form.get('event_date', '').strip()
        is_full_day  = request.form.get('is_full_day') == 'on'
        timeslot_ids = ','.join(request.form.getlist('timeslot_ids'))
        scope        = request.form.get('scope', 'school_wide')
        programme_id = request.form.get('programme_id', '').strip() or None
        outcome      = request.form.get('outcome', 'cancel')
        trimester    = request.form.get('trimester', '').strip() or None
        academic_year= request.form.get('academic_year', '').strip() or None
        is_recurring = request.form.get('is_recurring') == 'on'

        errors = []
        if not name:       errors.append('Event name is required.')
        if not event_date: errors.append('Event date is required.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('admin/event_add.html',
                                   programmes=programmes, timeslots=timeslots, form=request.form)

        from datetime import date as date_cls
        ev = Event(
            name         = name,
            description  = description or None,
            event_date   = date_cls.fromisoformat(event_date),
            is_full_day  = is_full_day,
            timeslot_ids = timeslot_ids if not is_full_day else None,
            scope        = scope,
            programme_id = int(programme_id) if programme_id else None,
            outcome      = outcome,
            trimester    = int(trimester) if trimester else None,
            academic_year= academic_year or None,
            is_recurring = is_recurring,
        )
        db.session.add(ev)
        db.session.commit()
        flash(f'Event "{name}" added successfully.', 'success')
        return redirect(url_for('admin.events'))

    return render_template('admin/event_add.html',
                           programmes=programmes, timeslots=timeslots, form={})


@admin_bp.route('/events/<int:event_id>/delete', methods=['POST'])
@login_required
def event_delete(event_id):
    from app.models.event import Event
    ev = Event.query.get_or_404(event_id)
    name = ev.name
    db.session.delete(ev)
    db.session.commit()
    flash(f'Event "{name}" deleted.', 'success')
    return redirect(url_for('admin.events'))
