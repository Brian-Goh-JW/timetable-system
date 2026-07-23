"""
STEP 32 — Switch 8 ENG programmes (CVE, MEC, METS, EEE, ISE, RSE, SBE, DSC)
from the raw Requirements_ENG source over to the team's cleaned data
('Provided Data/Cleaned data ENG cluster/*.xlsx'). Trimester 1 only for now
— these files also contain Tri 2 and Tri 3 sheets, deliberately deferred to
a later pass (confirmed with Brian 2026-07-10).

Each cleaned file also carries a real per-session "Venue" column (a room
code, not a full slot) for some programmes — NOT used yet. The solver still
picks every room itself; honoring these as hard room-pins would need a new
schema field (there's currently a fixed_timeslot_id for time but nothing
equivalent for room). Deferred, logged as a self-input gap on System Info.

Some cleaned rows use activity types with no weekly classroom slot at all
(Assignment, Capstone Project, Event, Projects, Work Attachment, "IWSP
(Career Skills)") — skipped from scheduling, same treatment as the existing
Practicum/Attachment/Internship skip list.

Rows referencing the 6 university-wide common modules already loaded from
'Requirements Template_ENG.xlsx' (bootstrap/31) — ENG1001, ENG1004, ENG1005,
ENG1008, ENG1010, ENG3001 — are left alone; those are already correctly
populated from their authoritative source. EEE/ISE reference a *different*,
newly-discovered common-module family (ENG1100-1104, ENG3100C-ENG3302C) not
covered by bootstrap/31 — imported here as ordinary per-programme sessions,
NOT yet cross-linked into SharedModuleGroups (flagged as a follow-up gap).

WIPES existing Tri-1 sessions for these 8 programmes' own modules before
reimporting (their own raw-sourced Tri-1 data becomes stale once replaced —
confirmed with Brian: reload cleanly rather than patch in place, since some
module codes differ between raw and cleaned, e.g. METS's cleaned Year 1 uses
MET1101/MET1300/MET1401 which don't exist under the old raw-loaded codes).

Run AFTER bootstrap/31 (needs the 6 protected common modules already loaded):
    python bootstrap/32_load_cleaned_eng_data.py
"""
import sys, os, secrets
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from app import create_app, db
from app.models.programme import Programme
from app.models.course import Course
from app.models.class_session import ClassSession
from app.models.class_session_professor import ClassSessionProfessor
from app.models.professor import Professor
from app.models.user import User
from app.models.student_group import StudentGroup
from app.models.timeslot import TimeSlot
from app.models.timetable_entry import TimetableEntry
from app.engine.template1_parser import load_module_sheet, PROG_NAMES, SKIP_NAMES

BASE = (r'C:\Users\brain\AppData\Local\Temp\claude\C--Users-brain-OneDrive-Documents-SIT-ProjectTimetable'
        r'\0e44374e-3794-4de2-873c-e66fbc6f7593\scratchpad\data_provided\Provided Data\Cleaned data ENG cluster')

FILES = {
    'CVE':  '(CVE) Civil Engineering.xlsx',
    'MEC':  '(MEC) Mechanical Engineering_.xlsx',
    'METS': '(METS) Mechatronics Systems.xlsx',
    'EEE':  'EEE (1).xlsx',
    'ISE':  'ISE.xlsx',
    'RSE':  'RSE.xlsx',
    'SBE':  'SBE.xlsx',
    'DSC':  'dsc_ (1).xlsx',
}

PROTECTED_COMMON_MODULES = {'ENG1001', 'ENG1004', 'ENG1005', 'ENG1008', 'ENG1010', 'ENG3001'}
# Not in SESSION_TYPE_MAP, so load_module_sheet already skips these rows silently
# (no weekly classroom slot to schedule) — tracked here only for the disclosure count.
NON_SCHEDULABLE_ACTIVITIES = {
    'assignment', 'capstone project', 'event', 'projects', 'work attachment',
    'iwsp (career skills)',
}
TRIMESTER = 1


def get_or_create_professor(name_raw, sid_raw):
    import re
    name = str(name_raw).strip() if name_raw is not None and not pd.isna(name_raw) else ''
    sid = str(sid_raw).strip() if sid_raw is not None and not pd.isna(sid_raw) else ''
    if not name or name.lower().strip() in SKIP_NAMES:
        return None
    if re.fullmatch(r't\d{1,2}', name.lower().strip()):
        return None
    name = name.title()
    sid = re.sub(r'[^\w]', '', sid)
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
        base_n = abs(hash(name)) % 9999
        sid = f'ENG{base_n:04d}'
        attempt = 0
        while Professor.query.filter_by(staff_id=sid).first():
            attempt += 1
            sid = f'ENG{(base_n + attempt) % 9999:04d}'
    email_local = re.sub(r'\.+', '.', re.sub(r'[^a-z0-9]', '.', name.lower())).strip('.')
    email = f'{email_local}@sit.edu.sg'
    attempt = 0
    while User.query.filter_by(email=email).first():
        attempt += 1
        email = f'{email_local}.{attempt}@sit.edu.sg'
    user = User(name=name, email=email, role='professor')
    user.set_password(secrets.token_urlsafe(24))
    db.session.add(user)
    db.session.flush()
    prof = Professor(user_id=user.id, staff_id=sid, department='ENG')
    db.session.add(prof)
    db.session.flush()
    return prof


def get_or_create_programme(code):
    prog = Programme.query.filter_by(code=code).first()
    if not prog:
        prog = Programme(code=code, name=PROG_NAMES.get(code, code), cluster='ENG')
        db.session.add(prog)
        db.session.flush()
        print(f'  [NEW] Programme: {code}')
    return prog


def get_or_create_student_group(prog, year_level, intake_size):
    label = f'{prog.code}-Y{year_level}'
    sg = StudentGroup.query.filter_by(programme_id=prog.id, year_level=year_level,
                                       group_label=label, parent_id=None).first()
    if not sg:
        sg = StudentGroup(programme_id=prog.id, year_level=year_level, group_label=label,
                           intake_size=intake_size or 30, parent_id=None)
        db.session.add(sg)
        db.session.flush()
        print(f'  [NEW] StudentGroup: {label} (size={intake_size})')
    elif intake_size and intake_size > 0 and sg.intake_size != intake_size:
        sg.intake_size = intake_size
    return sg


def get_or_create_course(module_code, title, prog, year_level, delivery_mode):
    course = Course.query.filter_by(module_code=module_code, programme_id=prog.id, trimester=TRIMESTER).first()
    if not course:
        course = Course(programme_id=prog.id, module_code=module_code, title=title or module_code,
                         year_level=year_level, trimester=TRIMESTER, delivery_mode=delivery_mode,
                         sessions_per_week=1, total_hours=0)
        db.session.add(course)
        db.session.flush()
    return course


app = create_app()
with app.app_context():
    all_slots = TimeSlot.query.all()

    wiped_sessions = 0
    wiped_courses = 0
    lost_fixed_pins = 0

    print('=== WIPE PHASE ===')
    for code in FILES:
        prog = Programme.query.filter_by(code=code).first()
        if not prog:
            continue
        courses = Course.query.filter_by(programme_id=prog.id, trimester=TRIMESTER).all()
        for c in courses:
            if c.module_code in PROTECTED_COMMON_MODULES:
                continue
            for s in list(c.class_sessions):
                if s.fixed_timeslot_id:
                    lost_fixed_pins += 1
                TimetableEntry.query.filter_by(class_session_id=s.id).delete()
                ClassSessionProfessor.query.filter_by(session_id=s.id).delete()
                if s.shared_module_group_id:
                    s.shared_module_group_id = None
                db.session.delete(s)
                wiped_sessions += 1
            db.session.delete(c)
            wiped_courses += 1
    db.session.commit()
    print(f'Wiped {wiped_courses} courses, {wiped_sessions} sessions ({lost_fixed_pins} had a manually-fixed timeslot, now lost).')

    print('\n=== IMPORT PHASE ===')
    created_sessions = 0
    skipped_common = 0
    skipped_activity = 0

    for prog_code, fname in FILES.items():
        fpath = os.path.join(BASE, fname)
        xl = pd.ExcelFile(fpath)
        tri1_sheet = [s for s in xl.sheet_names if 'Tri 1' in s][0]
        df_raw = pd.read_excel(fpath, sheet_name=tri1_sheet, header=None)

        activity_col_vals = df_raw.apply(
            lambda row: next((str(v).strip().lower() for v in row if isinstance(v, str)
                               and str(v).strip().lower() in NON_SCHEDULABLE_ACTIVITIES), None),
            axis=1,
        )
        skipped_activity += activity_col_vals.notna().sum()

        file_count = 0
        for rec in load_module_sheet(df_raw, all_slots, fname_hint=fname):
            module_code = rec['module_code']
            if module_code in PROTECTED_COMMON_MODULES:
                skipped_common += 1
                continue

            prog = get_or_create_programme(rec['prog_code'] or prog_code)
            sg = get_or_create_student_group(prog, rec['year_level'], rec['class_size'])
            course = get_or_create_course(module_code, rec['module_title'], prog, rec['year_level'], rec['delivery_mode'])

            cs = ClassSession(
                course_id=course.id, session_type=rec['session_type'], delivery_mode=rec['delivery_mode'],
                is_async=rec['is_async'], duration_hours=rec['duration_hours'], student_group_id=sg.id,
                trimester=TRIMESTER, teaching_weeks=rec['teaching_weeks'], group_label=rec['group_label'],
                preferred_timeslot_id=rec['pref_slot_id'],
            )
            db.session.add(cs)
            db.session.flush()
            created_sessions += 1
            file_count += 1

            is_first = True
            for staff_name, staff_id in rec['staff']:
                prof = get_or_create_professor(staff_name, staff_id)
                if not prof:
                    continue
                db.session.add(ClassSessionProfessor(session_id=cs.id, professor_id=prof.id,
                                                       is_primary=is_first, display_order=0 if is_first else 1))
                is_first = False

        db.session.commit()
        print(f'  [OK] {prog_code}: {file_count} sessions created')

    print(f'\nDone. {created_sessions} sessions created, {skipped_common} rows skipped (protected common modules), '
          f'{skipped_activity} rows skipped (non-schedulable activity type).')
