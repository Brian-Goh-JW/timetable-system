import os
import unittest
from datetime import time
from urllib.parse import urlparse

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['FLASK_SECRET_KEY'] = 'unit-test-secret'

from app import create_app, db
from app.engine.checker import get_blocking_issues, get_fixed_hard_constraint_conflicts
from app.models.class_session import ClassSession
from app.models.class_session_professor import ClassSessionProfessor
from app.models.course import Course
from app.models.professor import Professor
from app.models.programme import Programme
from app.models.room import Room
from app.models.student_group import StudentGroup
from app.models.timeslot import TimeSlot
from app.models.timetable_entry import TimetableEntry
from app.models.user import User
from app.models.solver_job import SolverJob
from app.routes.student import _student_timetable_uses_backbone
from app.routes.teacher import _teacher_timetable_uses_backbone


class ReadinessCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.context = cls.app.app_context()
        cls.context.push()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.context.pop()

    def setUp(self):
        db.session.rollback()
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()

    def test_stale_login_csrf_redirects_to_a_fresh_form(self):
        client = self.app.test_client()

        response = client.post('/login', data={
            'email': 'student@sit.edu.sg',
            'password': 'irrelevant',
        })

        self.assertEqual(303, response.status_code)
        self.assertEqual('/login', urlparse(response.location).path)
        refreshed = client.get(response.location)
        self.assertIn(b'login page expired', refreshed.data)

    def test_student_timetable_prefers_one_published_layer(self):
        programme = self._programme('LAY')
        group = StudentGroup(
            programme_id=programme.id,
            year_level=1,
            group_label='LAY-Y1',
            intake_size=20,
        )
        db.session.add(group)
        course = self._course(programme, trimester=1, split_count=1)
        db.session.flush()
        class_session = ClassSession(
            course_id=course.id,
            session_type='lecture',
            delivery_mode='online',
            is_async=False,
            duration_hours=2,
            student_group_id=group.id,
            trimester=1,
        )
        slot = TimeSlot(
            day_of_week='Monday',
            period_label='P1',
            start_time=time(9, 0),
            end_time=time(11, 0),
        )
        db.session.add_all([class_session, slot])
        db.session.flush()
        db.session.add(TimetableEntry(
            class_session_id=class_session.id,
            timeslot_id=slot.id,
            week_number=1,
            trimester='AY2526-T1',
            academic_year='AY2526',
            is_published=True,
            is_backbone=True,
        ))
        db.session.commit()

        self.assertTrue(_student_timetable_uses_backbone(
            'AY2526-T1', {group.id}
        ))
        self.assertTrue(_teacher_timetable_uses_backbone(
            'AY2526-T1', [class_session.id]
        ))

        db.session.add(TimetableEntry(
            class_session_id=class_session.id,
            timeslot_id=slot.id,
            week_number=1,
            trimester='AY2526-T1',
            academic_year='AY2526',
            is_published=True,
            is_backbone=False,
        ))
        db.session.commit()

        self.assertFalse(_student_timetable_uses_backbone(
            'AY2526-T1', {group.id}
        ))
        self.assertFalse(_teacher_timetable_uses_backbone(
            'AY2526-T1', [class_session.id]
        ))

    def _programme(self, code='TST'):
        programme = Programme(code=code, name=f'{code} Programme', cluster='Test')
        db.session.add(programme)
        db.session.flush()
        return programme

    def _course(self, programme, *, trimester, split_count):
        course = Course(
            programme_id=programme.id,
            module_code=f'TST{trimester}001',
            trimester=trimester,
            title='Test Module',
            year_level=1,
            delivery_mode='f2f',
            sessions_per_week=1,
            total_hours=24,
            split_count=split_count,
        )
        db.session.add(course)
        db.session.flush()
        return course

    def test_missing_split_is_scoped_to_requested_trimester(self):
        programme = self._programme()
        self._course(programme, trimester=2, split_count=None)
        db.session.commit()

        blockers, _ = get_blocking_issues(trimester_num=1)

        self.assertEqual([], blockers)

    def test_synchronous_session_without_group_blocks_generation(self):
        programme = self._programme()
        course = self._course(programme, trimester=1, split_count=1)
        db.session.add(ClassSession(
            course_id=course.id,
            session_type='lecture',
            delivery_mode='f2f',
            is_async=False,
            duration_hours=2,
            trimester=1,
        ))
        db.session.commit()

        blockers, _ = get_blocking_issues(trimester_num=1)

        self.assertTrue(any('no student group' in item for item in blockers))

    def test_asynchronous_session_without_group_is_allowed(self):
        programme = self._programme()
        course = self._course(programme, trimester=1, split_count=1)
        db.session.add(ClassSession(
            course_id=course.id,
            session_type='lecture',
            delivery_mode='online',
            is_async=True,
            duration_hours=1,
            trimester=1,
        ))
        db.session.commit()

        blockers, _ = get_blocking_issues(trimester_num=1)

        self.assertFalse(any('no student group' in item for item in blockers))

    def test_programme_scoping_isolates_bad_input(self):
        valid_programme = self._programme('GOOD')
        bad_programme = self._programme('BAD')
        self._course(valid_programme, trimester=1, split_count=1)
        bad_course = self._course(bad_programme, trimester=1, split_count=1)
        db.session.add(ClassSession(
            course_id=bad_course.id,
            session_type='lecture',
            delivery_mode='f2f',
            is_async=False,
            duration_hours=2,
            trimester=1,
        ))
        db.session.commit()

        blockers, _ = get_blocking_issues(
            trimester_num=1, programme_ids=[valid_programme.id]
        )

        self.assertEqual([], blockers)

    def _create_fixed_hard_clash(self):
        programme = self._programme('DSC')
        group = StudentGroup(
            programme_id=programme.id,
            year_level=1,
            group_label='DSC-Y1',
            intake_size=30,
        )
        db.session.add(group)
        first_course = Course(
            programme_id=programme.id,
            module_code='ASE1011',
            trimester=1,
            title='First Module',
            year_level=1,
            delivery_mode='f2f',
            sessions_per_week=1,
            total_hours=24,
            split_count=1,
        )
        second_course = Course(
            programme_id=programme.id,
            module_code='DSC1001',
            trimester=1,
            title='Second Module',
            year_level=1,
            delivery_mode='f2f',
            sessions_per_week=1,
            total_hours=24,
            split_count=1,
        )
        slot = TimeSlot(
            day_of_week='Monday',
            period_label='P1',
            start_time=time(9, 0),
            end_time=time(11, 0),
        )
        room = Room(
            room_code='E2-01-01',
            building='E2',
            capacity=60,
            room_type='lecture',
            is_active=True,
        )
        professor_user = User(
            name='David Lin Weidong',
            email='david@sit.edu.sg',
            password_hash='unused',
            role='professor',
        )
        db.session.add_all([first_course, second_course, slot, room, professor_user])
        db.session.flush()
        professor = Professor(
            user_id=professor_user.id,
            staff_id='P001',
            department='Test',
        )
        db.session.add(professor)
        db.session.flush()

        first = ClassSession(
            course_id=first_course.id,
            session_type='lecture',
            delivery_mode='f2f',
            is_async=False,
            duration_hours=2,
            student_group_id=group.id,
            fixed_timeslot_id=slot.id,
            fixed_room_id=room.id,
            trimester=1,
            teaching_weeks='1,2,3',
        )
        second = ClassSession(
            course_id=second_course.id,
            session_type='tutorial',
            delivery_mode='f2f',
            is_async=False,
            duration_hours=2,
            student_group_id=group.id,
            fixed_timeslot_id=slot.id,
            fixed_room_id=room.id,
            trimester=1,
            teaching_weeks='2,3,4',
        )
        db.session.add_all([first, second])
        db.session.flush()
        db.session.add_all([
            ClassSessionProfessor(
                session_id=first.id,
                professor_id=professor.id,
                is_primary=True,
                display_order=0,
            ),
            ClassSessionProfessor(
                session_id=second.id,
                professor_id=professor.id,
                is_primary=True,
                display_order=0,
            ),
        ])
        db.session.commit()

    def test_fixed_hard_clash_reports_h1_h2_and_h3(self):
        self._create_fixed_hard_clash()

        conflicts = get_fixed_hard_constraint_conflicts(trimester_num=1)

        self.assertEqual(1, len(conflicts))
        self.assertIn('Hard-constraint clash: ASE1011 (lecture) and DSC1001 (tutorial)', conflicts[0])
        self.assertIn('both fixed to Monday 09:00-11:00', conflicts[0])
        self.assertIn('H1 same room [E2-01-01]', conflicts[0])
        self.assertIn('H2 professor David Lin Weidong', conflicts[0])
        self.assertIn('H3 student group DSC-Y1', conflicts[0])

    def test_all_trimester_guard_does_not_compare_different_trimesters(self):
        self._create_fixed_hard_clash()
        second = (
            ClassSession.query
            .join(ClassSession.course)
            .filter(Course.module_code == 'DSC1001')
            .one()
        )
        second.trimester = 2
        second.course.trimester = 2
        db.session.commit()

        self.assertEqual([], get_fixed_hard_constraint_conflicts())

    def test_async_generation_guard_returns_all_issues_before_any_write(self):
        self._create_fixed_hard_clash()
        admin = User(
            name='Admin',
            email='admin@sit.edu.sg',
            password_hash='unused',
            role='admin',
        )
        db.session.add(admin)
        db.session.commit()
        entries_before = TimetableEntry.query.count()

        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(admin.id)
            session['_fresh'] = True
        csrf_was_enabled = self.app.config.get('WTF_CSRF_ENABLED', True)
        self.app.config['WTF_CSRF_ENABLED'] = False
        try:
            response = client.post('/admin/timetable/solve-async', data={
                'action': 'generate',
                'academic_year': 'AY2526',
                'trimester_num': '1',
                'start_date': '2025-09-01',
                'term_break_weeks': '7',
            })
        finally:
            self.app.config['WTF_CSRF_ENABLED'] = csrf_was_enabled
        # This test class deliberately keeps one application context for its
        # in-memory database. Flask-Login caches the request user on that
        # context, so clear only that cache before the next isolated test.
        from flask import g
        g.pop('_login_user', None)

        payload = response.get_json()
        self.assertEqual(409, response.status_code)
        self.assertTrue(payload['precheck_blocked'])
        self.assertEqual(1, payload['blocking_count'])
        self.assertEqual(0, payload['entries_created'])
        self.assertEqual(0, SolverJob.query.count())
        self.assertEqual(entries_before, TimetableEntry.query.count())


if __name__ == '__main__':
    unittest.main()
