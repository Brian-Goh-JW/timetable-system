"""Create a small synthetic dataset for evaluating every application role.

This never runs in production and never imports institutional or personal data.
Run after `flask db upgrade` on an empty development database.
"""

from datetime import date, time
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from app.models.programme import Programme
from app.models.course import Course
from app.models.student_group import StudentGroup
from app.models.user import User
from app.models.professor import Professor
from app.models.room import Room
from app.models.timeslot import TimeSlot
from app.models.class_session import ClassSession
from app.models.class_session_professor import ClassSessionProfessor
from app.models.timetable_entry import TimetableEntry
from app.models.student_enrollment import StudentEnrollment
from app.services.academic_terms import configure_term


TERM = 'AY2526-T1'
TEACHING_WEEKS = tuple(range(1, 7)) + tuple(range(8, 14))


def _account(name, email, role, password, group=None):
    user = User(
        name=name, email=email, role=role, student_group=group,
        active=True, must_change_password=False,
    )
    user.set_password(password)
    db.session.add(user)
    return user


def seed():
    app = create_app()
    if app.config.get('_APP_ENV') == 'production' or os.environ.get('APP_ENV', '').lower() == 'production':
        raise SystemExit('Synthetic demo seeding is disabled in production.')

    admin_password = os.environ.get('DEMO_ADMIN_PASSWORD', 'Test1234')
    student_password = os.environ.get('DEMO_STUDENT_PASSWORD', 'Test1234')
    professor_password = os.environ.get('DEMO_PROFESSOR_PASSWORD', 'Test1234')
    with app.app_context():
        if Programme.query.count() or User.query.count():
            raise SystemExit('Demo seed requires an empty database; existing data was not changed.')

        programme = Programme(
            code='DSC', name='Digital Supply Chain', cluster='Engineering',
        )
        db.session.add(programme)
        db.session.flush()
        group = StudentGroup(
            programme_id=programme.id, year_level=1,
            group_label='DSC-Y1', intake_size=40,
        )
        db.session.add(group)
        db.session.flush()

        _account('Demo Administrator', 'admin@example.com', 'admin', admin_password)
        professor_user = _account(
            'Demo Educator', 'professor@example.com', 'professor', professor_password,
        )
        student = _account(
            'Demo Student', 'student@example.com', 'student', student_password, group,
        )
        db.session.flush()
        professor = Professor(
            user_id=professor_user.id, staff_id='P-DEMO', department='Engineering',
            qualifications='digital supply chain, data analytics',
            max_weekly_hours=12, max_daily_hours=4, home_building='E2',
        )
        db.session.add(professor)

        lecture_room = Room(
            room_code='E2-DEMO-LT', building='E2', capacity=80,
            room_type='lecture', is_active=True,
            equipment='projector, lecture capture', is_accessible=True,
        )
        seminar_room = Room(
            room_code='E2-DEMO-SR', building='E2', capacity=50,
            room_type='seminar', is_active=True,
            equipment='projector, movable tables', is_accessible=True,
        )
        db.session.add_all([lecture_room, seminar_room])

        slots = []
        for day in ('Monday', 'Tuesday'):
            slots.extend([
                TimeSlot(
                    day_of_week=day, period_label='P1',
                    start_time=time(9), end_time=time(11),
                ),
                TimeSlot(
                    day_of_week=day, period_label='P2',
                    start_time=time(12), end_time=time(14),
                ),
            ])
        db.session.add_all(slots)
        db.session.flush()

        courses = [
            Course(
                programme_id=programme.id, module_code='DSC1001', trimester=1,
                title='Introduction to Digital Supply Chain', year_level=1,
                delivery_mode='f2f', sessions_per_week=2, total_hours=48,
                split_count=1,
            ),
            Course(
                programme_id=programme.id, module_code='DSC1002', trimester=1,
                title='Data Analytics Foundations', year_level=1,
                delivery_mode='f2f', sessions_per_week=2, total_hours=48,
                split_count=1,
            ),
        ]
        db.session.add_all(courses)
        db.session.flush()

        placements = [
            (courses[0], 'lecture', slots[0], lecture_room),
            (courses[0], 'tutorial', slots[3], seminar_room),
            (courses[1], 'lecture', slots[2], lecture_room),
            (courses[1], 'tutorial', slots[1], seminar_room),
        ]
        for course in courses:
            db.session.add(StudentEnrollment(
                user_id=student.id, course_id=course.id,
                academic_year='AY2526', trimester=1,
            ))
        for course, session_type, slot, room in placements:
            session = ClassSession(
                course_id=course.id, session_type=session_type,
                delivery_mode='f2f', is_async=False, duration_hours=2,
                student_group_id=group.id, trimester=1,
                teaching_weeks=','.join(str(week) for week in TEACHING_WEEKS),
                group_label='All', required_equipment='projector',
                accessibility_required=True,
            )
            db.session.add(session)
            db.session.flush()
            db.session.add(ClassSessionProfessor(
                session_id=session.id, professor_id=professor.id,
                is_primary=True, display_order=0,
            ))
            for week in TEACHING_WEEKS:
                db.session.add(TimetableEntry(
                    class_session_id=session.id, timeslot_id=slot.id, room_id=room.id,
                    week_number=week, trimester=TERM, academic_year='AY2526',
                    is_published=True, is_backbone=False,
                ))
        db.session.commit()
        configure_term('AY2526', 1, date(2025, 9, 1), {7})

    print('Synthetic demo created. Local-only credentials:')
    print(f'  admin@example.com / {admin_password}')
    print(f'  professor@example.com / {professor_password}')
    print(f'  student@example.com / {student_password}')


if __name__ == '__main__':
    seed()
