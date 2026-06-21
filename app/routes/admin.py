import math
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
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
    stats = {
        'total_courses':         Course.query.count(),
        'courses_missing_split': Course.query.filter(
                                     Course.delivery_mode.in_(['f2f', 'hybrid']),
                                     Course.split_count.is_(None)
                                 ).count(),
        'total_professors':      Professor.query.count(),
        'total_rooms':           Room.query.filter_by(is_active=True).count(),
        'open_flags':            TimetableFlag.query.filter_by(status='open').count(),
        'pending_declarations':  AvailabilityDeclaration.query.filter_by(status='pending').count(),
    }

    courses_missing_split = Course.query.filter(
        Course.delivery_mode.in_(['f2f', 'hybrid']),
        Course.split_count.is_(None)
    ).order_by(Course.year_level, Course.module_code).all()

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
    Look up the previous AY's equivalent trimester entries and build a dict:
      {(module_code, session_type): timeslot_id}
    Used as soft constraint so the solver prefers to keep sessions in the
    same slot as last year.
    """
    if not academic_year or len(academic_year) < 6:
        return {}
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
        key = (e.class_session.course.module_code.upper(), e.class_session.session_type)
        if key not in preferred:
            preferred[key] = e.timeslot_id
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
# Timetable — generate and view
# ---------------------------------------------------------------------------

@admin_bp.route('/timetable', methods=['GET', 'POST'])
@login_required
def timetable():
    from app.engine.checker import get_blocking_issues
    from app.engine.solver import solve
    from datetime import date

    issues   = get_blocking_issues()
    result   = None
    stats    = {}
    trimester = request.args.get('trimester', '')

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
                        ay_stats[tri_num] = {
                            'success': False,
                            'message': f'Tri {tri_num}: no start date provided — skipped.',
                            'stats'  : {},
                        }
                        continue

                    tri_issues = get_blocking_issues(trimester_num=tri_num)
                    if tri_issues:
                        ay_stats[tri_num] = {
                            'success': False,
                            'message': f'Tri {tri_num}: {len(tri_issues)} blocking issue(s) — skipped. '
                                       f'({tri_issues[0]}...)',
                            'stats'  : {},
                        }
                        continue

                    try:
                        start_date = date.fromisoformat(sd_raw)
                        # Option A — pin existing entries for this trimester
                        pinned_slots = None
                        if preserve:
                            existing = TimetableEntry.query.filter_by(trimester=tri_key).all()
                            seen_sess = set()
                            pinned_slots = {}
                            for e in existing:
                                if e.class_session_id not in seen_sess:
                                    seen_sess.add(e.class_session_id)
                                    pinned_slots[e.class_session_id] = e.timeslot_id

                        historical_preferred = _build_historical_preferred(academic_year, tri_num)

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
            tri_issues = get_blocking_issues(trimester_num=trimester_num) if trimester_num else issues

            if tri_issues:
                flash('Resolve all blocking issues for this trimester before generating.', 'danger')
                for iss in tri_issues[:5]:
                    flash(iss, 'warning')
            elif not academic_year:
                flash('Academic year is required (e.g. AY2526).', 'danger')
            elif not trimester_num:
                flash('Trimester number (1, 2, or 3) is required.', 'danger')
            elif not start_raw:
                flash('Start date is required.', 'danger')
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
                    # Option A — preserve existing slot assignments if requested
                    pinned_slots = None
                    preserve = request.form.get('preserve_existing') == 'on'
                    if preserve:
                        existing = TimetableEntry.query.filter_by(trimester=trimester).all()
                        seen_sess = set()
                        pinned_slots = {}
                        for e in existing:
                            if e.class_session_id not in seen_sess:
                                seen_sess.add(e.class_session_id)
                                pinned_slots[e.class_session_id] = e.timeslot_id

                    historical_preferred = _build_historical_preferred(academic_year, trimester_num)

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

            # Delete flag responses and flags first (FK references timetable_entries)
            flag_ids = [
                f.id for f in TimetableFlag.query
                .join(TimetableFlag.timetable_entry)
                .filter(TimetableEntry.trimester == trimester)
                .all()
            ]
            if flag_ids:
                FlagResponse.query.filter(FlagResponse.flag_id.in_(flag_ids)).delete(synchronize_session=False)
                TimetableFlag.query.filter(TimetableFlag.id.in_(flag_ids)).delete(synchronize_session=False)

            deleted = TimetableEntry.query.filter_by(trimester=trimester).delete()
            AcademicCalendar.query.filter_by(trimester=trimester).delete()
            db.session.commit()
            flash(f'Timetable for {trimester} cleared ({deleted} entries deleted).', 'info')
            trimester = ''

        elif action == 'reset_ay':
            # Clear all 3 trimesters for a full academic year at once
            from app.models.academic_calendar import AcademicCalendar
            from app.models.timetable_flag import TimetableFlag
            from app.models.flag_response import FlagResponse
            clear_ay = request.form.get('clear_ay', '').strip()
            if clear_ay:
                tri_keys = [f'{clear_ay}-T1', f'{clear_ay}-T2', f'{clear_ay}-T3']
                flag_ids = [
                    f.id for f in TimetableFlag.query
                    .join(TimetableFlag.timetable_entry)
                    .filter(TimetableEntry.trimester.in_(tri_keys))
                    .all()
                ]
                if flag_ids:
                    FlagResponse.query.filter(FlagResponse.flag_id.in_(flag_ids)).delete(synchronize_session=False)
                    TimetableFlag.query.filter(TimetableFlag.id.in_(flag_ids)).delete(synchronize_session=False)
                deleted = TimetableEntry.query.filter(TimetableEntry.trimester.in_(tri_keys)).delete(synchronize_session=False)
                AcademicCalendar.query.filter(AcademicCalendar.trimester.in_(tri_keys)).delete(synchronize_session=False)
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

    if trimester:
        if trimester.endswith('-all'):
            ay_prefix = trimester[:-4]  # 'AY2627'
            entries = (TimetableEntry.query
                       .filter(TimetableEntry.trimester.startswith(ay_prefix))
                       .join(TimetableEntry.timeslot)
                       .order_by(TimetableEntry.trimester, TimeSlot.day_of_week, TimeSlot.period_label)
                       .all())
        else:
            entries = (TimetableEntry.query
                       .filter_by(trimester=trimester)
                       .join(TimetableEntry.timeslot)
                       .order_by(TimeSlot.day_of_week, TimeSlot.period_label)
                       .all())

    # Collect programmes present in this trimester (before filtering, for filter UI)
    programmes = sorted(set(
        (e.class_session.course.programme.code, e.class_session.course.programme.name)
        for e in entries
        if e.class_session.course.programme
    ), key=lambda x: x[0]) if entries else []

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

        week_entries_raw = (TimetableEntry.query
                            .join(TimetableEntry.class_session)
                            .join(TimetableEntry.timeslot)
                            .filter(
                                TimetableEntry.trimester == trimester,
                                TimetableEntry.week_number == week_number,
                            )
                            .all())

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
                           prog_filter=prog_filter,
                           ay_default=ay_default,
                           ay_options=ay_options,
                           selected_tri=selected_tri)


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

    # Auto-select base/compare when exactly two AYs exist
    base_ay    = request.args.get('base_ay',    all_ays[0] if len(all_ays) >= 2 else '')
    compare_ay = request.args.get('compare_ay', all_ays[1] if len(all_ays) >= 2 else '')
    cross_rows = []

    if base_ay and compare_ay:
        def _build_map(trimester_key):
            entries = TimetableEntry.query.filter_by(trimester=trimester_key).all()
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
            """Infer why the solver moved a session off its historical slot."""
            cs = ClassSession.query.get(class_session_id)
            if cs:
                for prof in cs.all_professors:
                    if AvailabilityDeclaration.query.filter_by(
                        professor_id=prof.id,
                        timeslot_id=base_timeslot_id,
                    ).first():
                        return 'availability'
            if base_room_id:
                if TimetableEntry.query.filter_by(
                    trimester=compare_tri_key,
                    room_id=base_room_id,
                    timeslot_id=base_timeslot_id,
                ).first():
                    return 'room_taken'
            return 'shifted'

        for tri_num in [1, 2, 3]:
            base_map    = _build_map(f'{base_ay}-T{tri_num}')
            compare_map = _build_map(f'{compare_ay}-T{tri_num}')
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
                if consistency == 'different':
                    parts = []
                    if bd['day'] != cd['day']:
                        parts.append(f"Day: {bd['day'][:3]} → {cd['day'][:3]}")
                    if bd['start'] != cd['start']:
                        parts.append(f"Time: {bd['start']} → {cd['start']}")
                    if bd['room'] != cd['room']:
                        parts.append(f"Room: {bd['room'] or '?'} → {cd['room'] or '?'}")
                    reason = ' | '.join(parts) if parts else 'Slot changed'
                    reason_tag = _reason_tag(
                        bd['class_session_id'],
                        bd['timeslot_id'],
                        bd['room_id'],
                        f'{compare_ay}-T{tri_num}',
                    )
                elif consistency == 'base_only':
                    reason = f'Not scheduled in {compare_ay}'
                elif consistency == 'compare_only':
                    reason = f'New module in {compare_ay}'

                cross_rows.append({
                    'tri_num'     : tri_num,
                    'module_code' : mod,
                    'session_type': stype,
                    'base_slot'   : base_slot,
                    'compare_slot': compare_slot,
                    'consistency' : consistency,
                    'reason'      : reason,
                    'reason_tag'  : reason_tag,
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
                           all_ays=all_ays,
                           base_ay=base_ay,
                           compare_ay=compare_ay,
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
    from openpyxl.styles import Font, PatternFill, Alignment
    from flask import send_file

    trimester_filter = request.args.get('trimester', '')

    q = TimetableEntry.query
    if trimester_filter:
        q = q.filter_by(trimester=trimester_filter)
    entries = q.order_by(
        TimetableEntry.trimester,
        TimetableEntry.week_number,
    ).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Timetable'

    headers = [
        'AY', 'Trimester', 'Week', 'Day',
        'Start Time', 'End Time', 'Room',
        'Module Code', 'Module Name', 'Programme',
        'Year Level', 'Session Type', 'Delivery Mode', 'Professor(s)',
    ]
    header_fill = PatternFill('solid', fgColor='2E4057')
    header_font = Font(bold=True, color='FFFFFF')
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')

    for e in entries:
        ts  = e.timeslot
        cs  = e.class_session
        crs = cs.course
        profs = ', '.join(
            p.user.name for p in (e.override_professor,) if p
        ) or ', '.join(p.user.name for p in cs.all_professors)
        ws.append([
            e.academic_year or e.trimester[:6],
            e.trimester,
            e.week_number,
            ts.day_of_week,
            ts.start_time.strftime('%H:%M'),
            ts.end_time.strftime('%H:%M'),
            e.room.room_code if e.room else 'Online',
            crs.module_code,
            crs.title,
            crs.programme.code if crs.programme else '',
            crs.year_level,
            cs.session_type,
            cs.delivery_mode,
            profs,
        ])

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 40)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f'timetable_{trimester_filter or "all"}.xlsx'
    return send_file(buf, download_name=filename,
                     as_attachment=True,
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
