"""
STEP 44 — Data fix: create the missing "ASE-Y3" student group and assign it
to the 22 Year-3 ASE sessions that had none.

ASE3106, ASE3108, ASE3109, ASE3110 (all Trimester 1, Year 3, f2f) had zero
student_group_id set on any of their 22 sessions - not just a per-session
gap, but a missing cohort: unlike ASE-Y1 (75 students) and ASE-Y2 (62
students), no ASE-Y3 StudentGroup existed anywhere in the system. Without
a group, the solver can't size a room and deliberately skips scheduling
the class entirely (disclosed via the existing "no student group" warning)
rather than guess.

Confirmed with Brian (16 July 2026): create the group so these sessions
get scheduled, same "working system over zero-fabrication purity"
reasoning as the ASE2210 placeholder session earlier. intake_size is a
disclosed ESTIMATE (55), not real data - picked to continue ASE's observed
year-over-year decline (Y1=75, Y2=62), not measured. Disclosed on System
Info alongside the other self-input values.

Run ONCE:
    python bootstrap/44_add_ase_y3_group.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

from app import create_app, db
from app.models.class_session import ClassSession
from app.models.student_group import StudentGroup
from app.models.programme import Programme

ESTIMATED_INTAKE = 55  # disclosed estimate, continuing ASE's Y1=75 -> Y2=62 decline

app = create_app()

with app.app_context():
    prog = Programme.query.filter_by(code='ASE').first()
    existing = StudentGroup.query.filter_by(programme_id=prog.id, year_level=3).first()
    if existing:
        group = existing
        print(f'ASE-Y3 group already exists (id={group.id}) - reusing it.')
    else:
        group = StudentGroup(
            programme_id=prog.id, year_level=3,
            group_label='ASE-Y3', intake_size=ESTIMATED_INTAKE,
        )
        db.session.add(group)
        db.session.flush()
        print(f'Created ASE-Y3 group id={group.id}, intake_size={ESTIMATED_INTAKE} (estimated).')

    codes = ('ASE3106', 'ASE3108', 'ASE3109', 'ASE3110')
    sessions = (ClassSession.query.join(ClassSession.course)
                .filter(ClassSession.trimester == 1).all())
    updated = 0
    for s in sessions:
        if s.course.module_code in codes and s.student_group_id is None:
            s.student_group_id = group.id
            updated += 1
            print(f'  {s.course.module_code} {s.session_type} id={s.id} -> group {group.group_label}')

    db.session.commit()
    print(f'\nDone. {updated} session(s) assigned to ASE-Y3.')
