import os
import unittest

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['FLASK_SECRET_KEY'] = 'unit-test-secret'

from app import create_app, db
from app.engine.checker import get_blocking_issues
from app.models.class_session import ClassSession
from app.models.course import Course
from app.models.programme import Programme


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

    def _programme(self):
        programme = Programme(code='TST', name='Test Programme', cluster='Test')
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


if __name__ == '__main__':
    unittest.main()
