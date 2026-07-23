"""
STEP 33 — Load Trimester 2 and Trimester 3 for the 8 programmes already on
cleaned data (CVE, MEC, METS, EEE, ISE, RSE, SBE, DSC). Their cleaned files
each have a full Tri 2 / Tri 3 sheet that was deliberately left out of
bootstrap/32 (Tri 1 only, to keep that pass small and match the system's
scope at the time). The 8 remaining raw-data programmes have no Tri 2/3
source data at all (the raw upload only ever covered Trimester 1), so they
stay Tri-1-only after this script runs too.

Nothing to wipe here — Tri 2/3 sessions have never existed in this system
before, so this is a straightforward import, not a reload.

Cross-programme shared-module linking (the way ENG1001 etc. are linked for
Tri 1) is NOT attempted here. Tri 2/3 reference a large number of shared
module codes (ENG1002, ENG1004, ENG2100C-ENG4400C family and more) that
would need the same careful, hand-verified treatment as bootstrap/31 - out
of scope for this pass. They're imported as ordinary per-programme classes;
the gap is disclosed on System Info.

Run AFTER bootstrap/32:
    python bootstrap/33_load_cleaned_eng_tri23.py
"""
import sys, os, re, secrets
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from app import create_app, db
from app.models.programme import Programme
from app.models.course import Course
from app.models.student_group import StudentGroup
from app.models.class_session import ClassSession
from app.models.class_session_professor import ClassSessionProfessor
from app.models.professor import Professor
from app.models.user import User
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


def get_or_create_professor(name_raw, sid_raw):
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


def get_or_create_course(module_code, title, prog, year_level, delivery_mode, trimester):
    course = Course.query.filter_by(module_code=module_code, programme_id=prog.id, trimester=trimester).first()
    if not course:
        course = Course(programme_id=prog.id, module_code=module_code, title=title or module_code,
                         year_level=year_level, trimester=trimester, delivery_mode=delivery_mode,
                         sessions_per_week=1, total_hours=0)
        db.session.add(course)
        db.session.flush()
    return course


app = create_app()
with app.app_context():
    created_sessions = 0
    skipped_activity = 0

    for prog_code, fname in FILES.items():
        fpath = os.path.join(BASE, fname)
        xl = pd.ExcelFile(fpath)
        for trimester, sheet_pattern in [(2, 'Tri 2'), (3, 'Tri 3')]:
            sheet_matches = [s for s in xl.sheet_names if sheet_pattern in s]
            if not sheet_matches:
                print(f'  [SKIP] {prog_code} Tri {trimester}: no matching sheet')
                continue
            df_raw = pd.read_excel(fpath, sheet_name=sheet_matches[0], header=None)

            file_count = 0
            for rec in load_module_sheet(df_raw, all_slots=[], fname_hint=fname):
                module_code = rec['module_code']
                prog = get_or_create_programme(rec['prog_code'] or prog_code)
                sg = get_or_create_student_group(prog, rec['year_level'], rec['class_size'])
                course = get_or_create_course(module_code, rec['module_title'], prog,
                                               rec['year_level'], rec['delivery_mode'], trimester)

                cs = ClassSession(
                    course_id=course.id, session_type=rec['session_type'], delivery_mode=rec['delivery_mode'],
                    is_async=rec['is_async'], duration_hours=rec['duration_hours'], student_group_id=sg.id,
                    trimester=trimester, teaching_weeks=rec['teaching_weeks'], group_label=rec['group_label'],
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
            print(f'  [OK] {prog_code} Tri {trimester}: {file_count} sessions created')

    print(f'\nDone. {created_sessions} sessions created across Tri 2 and Tri 3.')
