"""
STEP 53 - Split classes locked to a real room smaller than their group size
into genuine parallel sections, instead of leaving one session claiming the
full cohort in an undersized room.

Brian, 2026-07-18: confirmed this pattern is normal and expected ("some
modules will have split like P1, P2 or T1, T2, T3 ... mainly because class
size is too big ... the professor teaches all of them") and explicitly
authorized fabricating the split sessions themselves, same standing rule as
every other generated-data fabrication in this project - the goal is a
Template 2 export that's actually usable, not a literal transcript of
whatever data survived import.

11 sessions (see System Info's "Classes locked to a real room smaller than
their group size") are locked to a real fixed_room_id whose capacity is
smaller than their full intake_size, with only ONE session on record - no
existing P1/P2-style sibling to divide the cohort across (unlike MET2002 and
MET4004, already fixed via ClassSession.effective_group_size). For each one:
  - N = ceil(intake_size / room capacity) parallel sections needed
  - create N-1 new ClassSession rows cloning the original (same course,
    session_type, delivery_mode, duration_hours, student_group_id,
    teaching_weeks, fixed_room_id - same real room, reused at a different
    time, exactly like MET4004's own real P1/P2 pattern)
  - copy the SAME professor assignment(s) to every new section (this is the
    normal case per Brian - one instructor runs all the parallel sections)
  - recompute group_label for every sibling of that (course, session_type)
    so the Template 2 "Group" column relabels correctly (P1/P2/P3/P4, or L/T
    for lecture/tutorial types)

Does NOT touch the shared StudentGroup.intake_size (still the true total
cohort figure) or run the solver - ClassSession.effective_group_size already
divides the shown Class Size fairly once these siblings exist and share
teaching weeks. A SEPARATE step (bootstrap/54) safely re-solves T1 with
every existing slot pinned, so only these new sessions get placed.

Run ONCE:
    python bootstrap/53_split_oversized_fixed_room_classes.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

import math
from app import create_app, db
from app.models.class_session import ClassSession
from app.models.class_session_professor import ClassSessionProfessor
from app.models.course import Course
from app.routes.admin import GROUP_LABEL_PREFIX, _recompute_group_labels

app = create_app()

# (module_code, session_type) - matched to the specific in-scope Course row
# with a fixed_room_id and an un-split "All" group_label.
TARGETS = [
    ('MET1101', 'lectorial'), ('MET1300', 'lab'), ('MET1300', 'lectorial'),
    ('MET1401', 'lectorial'), ('MET2502', 'lab'), ('MET4305', 'lab'),
    ('ENG1102', 'lab'), ('ENG1103', 'lab'), ('ENG1104', 'lab'),
    ('ENG2301C', 'lab'), ('ENG3104C', 'lab'),
]

with app.app_context():
    touched_pairs = set()
    new_session_ids = []

    for code, stype in TARGETS:
        courses = Course.query.filter_by(module_code=code).all()
        target = None
        for course in courses:
            sessions = ClassSession.query.filter_by(course_id=course.id, session_type=stype).all()
            for s in sessions:
                if s.fixed_room_id and s.group_label == 'All' and s.student_group:
                    target = s
                    break
            if target:
                break
        if not target:
            print(f'  [SKIP] {code} {stype}: no matching un-split fixed-room session found')
            continue

        room = target.fixed_room
        n = math.ceil(target.student_group.intake_size / room.capacity)
        if n < 2:
            print(f'  [SKIP] {code} {stype}: already fits ({target.student_group.intake_size} <= {room.capacity})')
            continue

        profs = list(target.professor_assignments)
        print(f'  {code} {stype} (session {target.id}): {target.student_group.intake_size} students, '
              f'{room.room_code} cap {room.capacity} -> splitting into {n} sections, '
              f'professor(s): {[a.professor.user.name for a in profs]}')

        for _ in range(n - 1):
            new_s = ClassSession(
                course_id=target.course_id,
                session_type=target.session_type,
                delivery_mode=target.delivery_mode,
                is_async=target.is_async,
                duration_hours=target.duration_hours,
                student_group_id=target.student_group_id,
                fixed_room_id=target.fixed_room_id,
                trimester=target.trimester,
                teaching_weeks=target.teaching_weeks,
                deferred_from_solve=target.deferred_from_solve,
            )
            db.session.add(new_s)
            db.session.flush()
            new_session_ids.append(new_s.id)
            for a in profs:
                db.session.add(ClassSessionProfessor(
                    session_id=new_s.id, professor_id=a.professor_id,
                    is_primary=a.is_primary, display_order=a.display_order,
                ))
        touched_pairs.add((target.course_id, target.session_type))

    for course_id, session_type in touched_pairs:
        _recompute_group_labels(course_id, session_type)

    db.session.commit()
    print(f'\n{len(new_session_ids)} new session(s) created: {new_session_ids}')
    print(f'{len(touched_pairs)} (course, session_type) pair(s) relabelled.')
