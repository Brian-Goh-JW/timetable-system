import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['FLASK_SECRET_KEY'] = 'unit-test-secret'


from app.engine.solver import (
    _build_group_session_families,
    _collapse_for_overlap,
    _room_compatible,
    _room_domain_with_fixed_pin,
    _programme_session_components,
)


def session(
    session_id,
    *,
    group_id=1,
    shared_group_id=None,
    fixed_timeslot_id=None,
    session_type='lab',
    effective_group_size=20,
    fixed_room_id=None,
    programme_id=1,
    programme_code='TST',
):
    return SimpleNamespace(
        id=session_id,
        student_group_id=group_id,
        shared_module_group_id=shared_group_id,
        fixed_timeslot_id=fixed_timeslot_id,
        session_type=session_type,
        student_group=SimpleNamespace(intake_size=60),
        effective_group_size=effective_group_size,
        fixed_room_id=fixed_room_id,
        course=SimpleNamespace(
            programme_id=programme_id,
            programme=SimpleNamespace(code=programme_code),
        ),
    )


class SolverLogicTests(unittest.TestCase):
    def test_independent_fixed_sessions_remain_in_overlap_checks(self):
        sessions = [
            session(1, fixed_timeslot_id=7),
            session(2, fixed_timeslot_id=7),
        ]

        self.assertEqual([1, 2], [s.id for s in _collapse_for_overlap(sessions)])

    def test_jointly_taught_records_collapse_to_one_overlap_interval(self):
        sessions = [
            session(1, shared_group_id=12),
            session(2, shared_group_id=12),
        ]

        self.assertEqual([1], [s.id for s in _collapse_for_overlap(sessions)])

    def test_room_capacity_uses_effective_split_group_size(self):
        split_session = session(1, effective_group_size=20)
        fitting_room = SimpleNamespace(room_type='lab', capacity=20)
        undersized_room = SimpleNamespace(room_type='lab', capacity=19)

        self.assertTrue(_room_compatible(fitting_room, split_session))
        self.assertFalse(_room_compatible(undersized_room, split_session))

    def test_valid_fixed_room_remains_the_only_allowed_room(self):
        rooms = [
            SimpleNamespace(id=1, room_type='lab', capacity=20),
            SimpleNamespace(id=2, room_type='lab', capacity=30),
        ]
        fixed_session = session(1, effective_group_size=20, fixed_room_id=2)

        domain, pin_applies = _room_domain_with_fixed_pin(
            rooms, fixed_session, {room.id: i for i, room in enumerate(rooms)},
        )

        self.assertEqual([1], domain)
        self.assertTrue(pin_applies)

    def test_wrong_type_fixed_room_is_dropped_for_safe_alternative(self):
        rooms = [
            SimpleNamespace(id=1, room_type='lab', capacity=30),
            SimpleNamespace(id=2, room_type='lecture', capacity=80),
        ]
        quiz = session(
            1, session_type='quiz', effective_group_size=60, fixed_room_id=1,
        )

        domain, pin_applies = _room_domain_with_fixed_pin(
            rooms, quiz, {room.id: i for i, room in enumerate(rooms)},
        )

        self.assertEqual([1], domain)
        self.assertFalse(pin_applies)

    def test_undersized_fixed_room_is_dropped_for_safe_alternative(self):
        rooms = [
            SimpleNamespace(id=1, room_type='lecture', capacity=30),
            SimpleNamespace(id=2, room_type='lecture', capacity=80),
        ]
        quiz = session(
            1, session_type='quiz', effective_group_size=60, fixed_room_id=1,
        )

        domain, pin_applies = _room_domain_with_fixed_pin(
            rooms, quiz, {room.id: i for i, room in enumerate(rooms)},
        )

        self.assertEqual([1], domain)
        self.assertFalse(pin_applies)

    def test_programme_components_never_split_one_programme(self):
        sessions = [
            session(1, programme_id=10, programme_code='AAA'),
            session(2, programme_id=10, programme_code='AAA'),
            session(3, programme_id=20, programme_code='BBB'),
        ]

        components = _programme_session_components(sessions)

        self.assertEqual(2, len(components))
        aaa = next(c for c in components if c['programme_codes'] == ['AAA'])
        self.assertEqual([1, 2], aaa['session_ids'])

    def test_shared_module_unions_complete_programmes(self):
        sessions = [
            session(1, programme_id=10, programme_code='AAA', shared_group_id=9),
            session(2, programme_id=10, programme_code='AAA'),
            session(3, programme_id=20, programme_code='BBB', shared_group_id=9),
            session(4, programme_id=20, programme_code='BBB'),
        ]

        components = _programme_session_components(sessions)

        self.assertEqual(1, len(components))
        self.assertEqual(['AAA', 'BBB'], components[0]['programme_codes'])
        self.assertEqual([1, 2, 3, 4], components[0]['session_ids'])

    def test_parent_and_subgroup_sessions_share_one_family(self):
        sessions = [
            session(1, group_id=10),
            session(2, group_id=11),
        ]
        conflict_map = {10: {10, 11}, 11: {10, 11}}

        with patch(
            'app.engine.solver._conflicting_group_ids',
            side_effect=lambda group_id: conflict_map[group_id],
        ):
            families = _build_group_session_families(sessions)

        self.assertEqual(1, len(families))
        self.assertEqual({1, 2}, {s.id for s in next(iter(families.values()))})


if __name__ == '__main__':
    unittest.main()
