"""
Pre-solver data fixes for AY2526 timetable generation.
Run AFTER bootstrap/18, BEFORE bootstrap/19.

Fixes:
  1. INF2008 lab -- create a StudentGroup (size=30) so the solver
     uses a small lab room instead of E2-08-01 (cap=100).
  2. UCS1001 seminars -- assign one unique professor per student-group pair
     (S6/S7/S8) so parallel sessions at the same timeslot are feasible.
     Also remove any professor shared with INF2008 lab from DSC2602 lab.
  3. UCS1001 seminar student groups -- set parent_id=NULL so the solver
     treats S6/S7/S8 as independent parallel groups (no cross-conflict).
"""
import sys
sys.path.insert(0, r'C:\Users\brain\OneDrive\Documents\SIT\ProjectTimetable')

from app import create_app, db
from app.models.course import Course
from app.models.class_session import ClassSession
from app.models.student_group import StudentGroup
from app.models.class_session_professor import ClassSessionProfessor

app = create_app()
with app.app_context():

    def get_lab(module_code):
        c = Course.query.filter_by(module_code=module_code).first()
        return ClassSession.query.filter_by(course_id=c.id, session_type='lab').first() if c else None

    # ── Fix 1: INF2008 lab student group ────────────────────────────────────
    print('\n-- Fix 1: INF2008 lab student group --')
    inf_lab = get_lab('INF2008')
    if not inf_lab:
        print('  INF2008 lab not found, skipping.')
    elif inf_lab.student_group_id is not None and inf_lab.student_group.intake_size <= 30:
        print('  Already fixed (size already <= 30).')
    else:
        # Find the DSC-Y2 parent group to attach to
        parent_sg = StudentGroup.query.filter_by(group_label='DSC-Y2').first()
        if not parent_sg:
            parent_sg = StudentGroup.query.filter_by(year_level=2, parent_id=None).first()

        existing_lab_sg = StudentGroup.query.filter_by(group_label='DSC-Y2-Lab').first()
        if existing_lab_sg:
            inf_lab.student_group_id = existing_lab_sg.id
            print(f'  Reused existing DSC-Y2-Lab (sg_id={existing_lab_sg.id})')
        elif parent_sg:
            new_sg = StudentGroup(
                programme_id=parent_sg.programme_id,
                year_level=parent_sg.year_level,
                group_label='DSC-Y2-Lab',
                intake_size=30,
                parent_id=parent_sg.id,
            )
            db.session.add(new_sg)
            db.session.flush()
            inf_lab.student_group_id = new_sg.id
            print(f'  Created DSC-Y2-Lab (sg_id={new_sg.id}, size=30) for INF2008 lab cs_id={inf_lab.id}')
        else:
            print('  No suitable parent group found; skipping.')

    # ── Fix 2: UCS1001 professor assignments + DSC2602 lab ──────────────────
    print('\n-- Fix 2: UCS1001 professor assignments --')
    ucs_course = Course.query.filter_by(module_code='UCS1001').first()
    if not ucs_course:
        print('  UCS1001 not found, skipping.')
    else:
        seminar_sessions = (ClassSession.query
                            .filter_by(course_id=ucs_course.id, session_type='seminar')
                            .order_by(ClassSession.id).all())

        # Collect all professor IDs currently assigned to any UCS1001 seminar
        all_prof_ids = sorted({
            csp.professor_id
            for cs in seminar_sessions
            for csp in ClassSessionProfessor.query.filter_by(session_id=cs.id).all()
        })
        print(f'  Found {len(seminar_sessions)} seminar sessions, {len(all_prof_ids)} unique professors.')

        # Group sessions by student_group_id and assign one professor each
        sessions_by_group = {}
        for cs in seminar_sessions:
            sessions_by_group.setdefault(cs.student_group_id, []).append(cs)

        for i, (sg_id, group_sessions) in enumerate(sessions_by_group.items()):
            sole_prof = all_prof_ids[i % len(all_prof_ids)] if all_prof_ids else None
            for cs in group_sessions:
                deleted = ClassSessionProfessor.query.filter_by(session_id=cs.id).delete()
                if sole_prof:
                    db.session.add(ClassSessionProfessor(
                        session_id=cs.id, professor_id=sole_prof,
                        is_primary=True, display_order=0,
                    ))
                print(f'  cs_id={cs.id} (sg={sg_id}): removed {deleted}, assigned prof_id={sole_prof}')

        # Remove professors shared between DSC2602 lab and INF2008 lab
        dsc2602_lab = get_lab('DSC2602')
        if dsc2602_lab and inf_lab:
            inf_prof_ids = {
                csp.professor_id
                for csp in ClassSessionProfessor.query.filter_by(session_id=inf_lab.id).all()
            }
            dsc_prof_ids = {
                csp.professor_id
                for csp in ClassSessionProfessor.query.filter_by(session_id=dsc2602_lab.id).all()
            }
            shared = inf_prof_ids & dsc_prof_ids
            for prof_id in shared:
                ClassSessionProfessor.query.filter_by(
                    session_id=dsc2602_lab.id, professor_id=prof_id
                ).delete()
                print(f'  DSC2602 lab cs_id={dsc2602_lab.id}: removed shared prof_id={prof_id}')
            if not shared:
                print(f'  DSC2602 lab: no shared professors with INF2008 lab.')

    # ── Fix 3: UCS1001 seminar student groups — remove DSC-Y1 parent ────────
    print('\n-- Fix 3: UCS1001 seminar student groups --')
    if ucs_course:
        for cs in seminar_sessions:
            sg = cs.student_group
            if sg is None:
                continue
            if sg.parent_id is not None:
                print(f'  sg_id={sg.id} ({sg.group_label}): parent_id={sg.parent_id} -> None')
                sg.parent_id = None
                sg.intake_size = 25
            else:
                print(f'  sg_id={sg.id} ({sg.group_label}): already independent.')

    db.session.commit()
    print('\nAll fixes applied. Now run bootstrap/19_regenerate_ay2526.py')
