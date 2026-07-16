"""
STEP 41 — Data fix: give ASE2210 (Trimester 1, course id 36) a real,
schedulable session.

Follows STEP 40, which removed ASE2210's only 3 sessions (2 Lectures, 1
Quiz) because every one referenced a teaching week (14 or 15) outside this
system's 13-week calendar - confirmed with Brian as not a real value. That
left ASE2210 with zero sessions, which blocked ALL of T1 from generating
(checker.py: an f2f course with no split_count and no sessions is a hard
blocker), and exposed a deeper problem: ASE2210 never had any genuinely
schedulable T1 data to begin with, not just a bad week number.

Confirmed with Brian (16 July 2026): the dataset is already synthetic and
Ms. Yang knows this - the goal is a working system that takes whatever
data exists and produces a usable Template 2 output, not zero-fabrication
purity. Rather than delete the module entirely (which would silently drop
it from the output), added one basic Lecture session with no hard
constraints (no fixed timeslot/room, no professor - the solver places it
freely, same as any normal session), matching this trimester's dominant
pattern for every other lecture: 2 hours, the standard "all 13 weeks minus
the week 7 term break" schedule, and the same ASE-Y2 student group (id 9)
the original (deleted) sessions used.

Run ONCE:
    python bootstrap/41_add_ase2210_placeholder_session.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

from app import create_app, db
from app.models.class_session import ClassSession
from app.models.course import Course

app = create_app()

with app.app_context():
    course = Course.query.filter_by(module_code='ASE2210', trimester=1).first()
    if course is None:
        print('ASE2210 (trimester 1) not found - nothing to do.')
        sys.exit(1)
    if course.class_sessions:
        print(f'ASE2210 already has {len(course.class_sessions)} session(s) - nothing to do.')
        sys.exit(0)

    session = ClassSession(
        course_id        = course.id,
        session_type     = 'lecture',
        delivery_mode    = 'f2f',
        duration_hours   = 2,
        student_group_id = 9,   # ASE-Y2
        trimester        = 1,
        teaching_weeks   = '1,2,3,4,5,6,8,9,10,11,12,13',
        group_label      = 'All',  # single, unsplit session
    )
    db.session.add(session)
    db.session.commit()
    print(f'Added session id={session.id} for ASE2210 (course id={course.id}): '
          f'lecture, 2h, weeks 1-13 (minus term break), group ASE-Y2, no hard constraints.')
