"""
Bootstrap 26 — Fix METS-Y4 sessions that were mistakenly loaded as METS-Y2.

Root cause: normalise_prog_year('METS/2022') matched pattern r'^([A-Z]+)\s*/\s*(\d)'
and extracted '2' (first digit of 2022), assigning all METS Y4 modules to METS-Y2.

This script:
  1. Deletes the 10 MET4xxx sessions wrongly assigned to METS-Y2
  2. Re-runs the METS_Year 4.xlsx import with the corrected parser (bootstrap/23 was fixed)

Run AFTER bootstrap/23 parser fix:
    python bootstrap/26_fix_mets_y4.py
"""
import sys, os, re
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from datetime import time as dtime
from app import create_app, db
from app.models.programme import Programme
from app.models.course import Course
from app.models.class_session import ClassSession
from app.models.student_group import StudentGroup
from app.models.professor import Professor
from app.models.user import User
from app.models.timeslot import TimeSlot
from app.models.class_session_professor import ClassSessionProfessor
from app.models.timetable_entry import TimetableEntry

DATA_FILE = r'C:\Users\brain\Downloads\Data_extracted\Data\Requirements_ENG\METS_Year 4.xlsx'
TRIMESTER = 1

# --- Helpers (copied from bootstrap/23 with the fixed normalise_prog_year) ---

SKIP_ACTIVITIES = frozenset({'practicum', 'field trip', 'fieldwork', 'attachment', 'internship'})
SESSION_TYPE_MAP = {
    'lecture': 'lecture', 'lectorial': 'lectorial', 'tutorial': 'tutorial',
    'lab': 'lab', 'laboratory': 'lab', 'workshop': 'workshop',
    'quiz': 'quiz', 'seminar': 'seminar',
}
DELIVERY_MAP = {
    'f2f': ('f2f', False), 'ftf': ('f2f', False),
    'online - synchronous': ('online', False), 'online-synchronous': ('online', False),
    'online synchronous': ('online', False),
    'online - asynchronous': ('online', True), 'online-asynchronous': ('online', True),
    'online asynchronous': ('online', True), 'online': ('online', False),
    'hybrid': ('f2f', False),
}
DAY_MAP = {
    'mon': 'Monday', 'monday': 'Monday', 'tue': 'Tuesday', 'tuesday': 'Tuesday',
    'wed': 'Wednesday', 'wednesday': 'Wednesday', 'thu': 'Thursday', 'thursday': 'Thursday',
    'fri': 'Friday', 'friday': 'Friday',
}
DURATION_DEFAULTS = {
    'lecture': 2, 'lectorial': 2, 'tutorial': 2,
    'lab': 3, 'workshop': 3, 'quiz': 2, 'seminar': 2,
}
SKIP_NAMES = frozenset({
    'nan', 'john doe', 'jane smith', 'temp staff', 'tbc', 'tbd',
    't1', 't2', 't3', 't4', 't5', 'tba', 'n/a', '-',
})


def normalise_prog_year(raw):
    if pd.isna(raw):
        return None, None
    s = str(raw).strip()
    s = re.sub(r'\n.*', '', s).strip()
    s = re.sub(r'\s+and\s+\w+\s+(?:cbe|common)?\b', '', s, flags=re.IGNORECASE).strip()
    s_up = s.upper()

    intake_m = re.search(r'^([A-Z]+)\s*/\s*(20\d{2})\b', s_up)
    if intake_m:
        intake_year = int(intake_m.group(2))
        year_level = 2026 - intake_year
        if 1 <= year_level <= 5:
            return intake_m.group(1).upper(), year_level

    patterns = [
        r'^([A-Z]+)\s*/\s*(?:YR|Y)\s*(\d)(?!\d)',
        r'^([A-Z]+)\s+(?:YR|YEAR)\s*(\d)(?!\d)',
        r'^([A-Z]+)\s*/\s*(?:YEAR)\s*(\d)(?!\d)',
        r'^([A-Z]+)\s*/\s*(\d)(?!\d)',
        r'^([A-Z]+)\s+(?:CBE|COMMON).*?(?:YR|YEAR|Y)\s*(\d)(?!\d)',
    ]
    for pat in patterns:
        m = re.search(pat, s_up)
        if m:
            return m.group(1).upper(), int(m.group(2))
    return None, None


def parse_time_str(s):
    if not s:
        return None
    s = str(s).strip().lower().replace(' ', '')
    if re.fullmatch(r'\d{3,4}', s):
        s = s.zfill(4)
        try:
            return dtime(int(s[:2]), int(s[2:]))
        except ValueError:
            return None
    m = re.match(r'(\d{1,2}):(\d{2})', s)
    if m:
        try:
            return dtime(int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def normalise_weeks(raw):
    if pd.isna(raw):
        return None
    s = str(raw).strip()
    if re.match(r'\d{4}-\d{2}-\d{2}', s):
        return None
    parts = re.findall(r'\d+', s)
    if parts:
        weeks = sorted(set(int(p) for p in parts if 1 <= int(p) <= 52))
        return ','.join(str(w) for w in weeks)
    return None


def build_col_map(header_vals):
    col_map = {}
    for c in header_vals:
        lc = str(c).strip().lower()
        if 'prog' in lc and ('yr' in lc or 'year' in lc):
            col_map[c] = 'prog_yr'
        elif 'class size' in lc:
            col_map[c] = 'class_size'
        elif 'module name' in lc:
            col_map[c] = 'module_name'
        elif 'module code' in lc:
            col_map[c] = 'module_code'
        elif 'activity' in lc:
            col_map[c] = 'activity'
        elif 'delivery mode' in lc:
            col_map[c] = 'delivery_mode'
        elif 'teaching weeks' in lc or lc == 'weeks':
            col_map[c] = 'teaching_weeks'
        elif lc == 'staff 1':
            col_map[c] = 'staff1'
        elif 'staff id 1' in lc:
            col_map[c] = 'staff_id1'
        elif lc == 'staff 2':
            col_map[c] = 'staff2'
        elif 'staff id 2' in lc:
            col_map[c] = 'staff_id2'
        elif 'remark' in lc or 'note' in lc:
            col_map[c] = 'remarks'
    return col_map


def get_or_create_professor(name_raw, sid_raw, app_context):
    name = str(name_raw).strip() if not pd.isna(name_raw) else ''
    sid = str(sid_raw).strip() if not pd.isna(sid_raw) else ''
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
        base = abs(hash(name)) % 9999
        sid = f'ENG{base:04d}'
        attempt = 0
        while Professor.query.filter_by(staff_id=sid).first():
            attempt += 1
            sid = f'ENG{(base + attempt) % 9999:04d}'
    email_local = re.sub(r'[^a-z0-9]', '.', name.lower()).strip('.')
    email_local = re.sub(r'\.+', '.', email_local)
    email = f'{email_local}@sit.edu.sg'
    attempt = 0
    while User.query.filter_by(email=email).first():
        attempt += 1
        email = f'{email_local}.{attempt}@sit.edu.sg'
    user = User(name=name, email=email, role='professor')
    user.set_password('SIT@2526')
    db.session.add(user)
    db.session.flush()
    prof = Professor(user_id=user.id, staff_id=sid, department='ENG')
    db.session.add(prof)
    db.session.flush()
    return prof


app = create_app()
with app.app_context():
    # ── Step 1: Remove MET4xxx sessions wrongly assigned to METS-Y2 ──────────
    prog_mets = Programme.query.filter_by(code='METS').first()
    if not prog_mets:
        print('ERROR: METS programme not found.')
        sys.exit(1)

    sg_y2 = StudentGroup.query.filter_by(
        programme_id=prog_mets.id, group_label='METS-Y2').first()

    removed = 0
    if sg_y2:
        wrong_sessions = (
            ClassSession.query
            .join(Course, ClassSession.course_id == Course.id)
            .filter(
                ClassSession.student_group_id == sg_y2.id,
                ClassSession.trimester == TRIMESTER,
                Course.module_code.like('MET4%'),
            ).all()
        )
        for cs in wrong_sessions:
            print(f'  REMOVE  id={cs.id} module={cs.course.module_code} '
                  f'type={cs.session_type} (was misassigned to METS-Y2)')
            # Delete timetable entries first (class_session_id is NOT NULL)
            TimetableEntry.query.filter_by(class_session_id=cs.id).delete()
            ClassSessionProfessor.query.filter_by(session_id=cs.id).delete()
            db.session.delete(cs)
            removed += 1
        db.session.commit()
        print(f'  Removed {removed} wrong METS-Y2/MET4xxx sessions.\n')
    else:
        print('  METS-Y2 group not found — no cleanup needed.\n')

    # ── Step 2: Load METS_Year 4.xlsx with fixed parser ──────────────────────
    all_slots = TimeSlot.query.all()

    seen = set()
    for cs in ClassSession.query.join(Course).all():
        seen.add((cs.course.module_code, cs.course.programme_id, cs.session_type,
                  cs.group_label or 'All', cs.teaching_weeks or ''))

    df_raw = pd.read_excel(DATA_FILE, sheet_name='Module', header=None)
    header_row_idx = None
    for i, row in df_raw.iterrows():
        vals = [str(v).strip().lower() for v in row if not pd.isna(v) and str(v).strip()]
        if 'prog/yr' in vals or ('module code' in vals and 'activity' in vals):
            header_row_idx = i
            break
    if header_row_idx is None:
        print('ERROR: Header row not found in METS_Year 4.xlsx')
        sys.exit(1)

    col_map = build_col_map(df_raw.iloc[header_row_idx].tolist())
    df = df_raw.iloc[header_row_idx + 1:].copy()
    df.columns = df_raw.iloc[header_row_idx].tolist()
    df = df.rename(columns=col_map)
    df = df.dropna(how='all')

    current_prog = 'METS'
    current_year = 4
    current_size = None
    current_module = None
    current_title = None
    created = 0

    for _, row in df.iterrows():
        row = row.to_dict()

        py_raw = row.get('prog_yr')
        if py_raw is not None and not pd.isna(py_raw) and str(py_raw).strip():
            v = str(py_raw).strip()
            if v.lower() not in ('nan', 'prog/yr'):
                pc, yl = normalise_prog_year(v)
                print(f'  Prog/Yr "{v}" → ({pc}, {yl})')
                if pc and yl:
                    current_prog, current_year = pc, yl
                elif pc:
                    current_prog = pc
                cs_raw = row.get('class_size')
                if cs_raw is not None and not pd.isna(cs_raw):
                    try:
                        current_size = int(float(str(cs_raw)))
                    except (ValueError, TypeError):
                        pass

        mc_raw = row.get('module_code')
        if mc_raw is not None and not pd.isna(mc_raw) and str(mc_raw).strip():
            v = str(mc_raw).strip().upper()
            if v.lower() not in ('nan', 'module code'):
                current_module = v.split('/')[0].strip()
                mn_raw = row.get('module_name')
                current_title = (str(mn_raw).strip()
                                 if mn_raw is not None and not pd.isna(mn_raw)
                                 else current_module)

        act_raw = row.get('activity')
        if act_raw is None or pd.isna(act_raw) or not str(act_raw).strip():
            continue
        if not current_module:
            continue
        act_str = str(act_raw).strip().lower()
        if act_str in SKIP_ACTIVITIES:
            continue
        session_type = SESSION_TYPE_MAP.get(act_str)
        if not session_type:
            continue
        if not current_prog or not current_year:
            continue

        dm_raw = row.get('delivery_mode')
        dm_str = str(dm_raw).strip().lower() if dm_raw is not None and not pd.isna(dm_raw) else 'f2f'
        delivery_mode, is_async = DELIVERY_MAP.get(dm_str, ('f2f', False))
        teaching_weeks = normalise_weeks(row.get('teaching_weeks'))
        duration_hours = DURATION_DEFAULTS.get(session_type, 2)

        # Get/create programme, student group, course
        prog = Programme.query.filter_by(code=current_prog).first()
        if not prog:
            print(f'  SKIP: Programme {current_prog} not found')
            continue

        label = f'{prog.code}-Y{current_year}'
        sg = StudentGroup.query.filter_by(
            programme_id=prog.id, year_level=current_year, group_label=label,
            parent_id=None).first()
        if not sg:
            sg = StudentGroup(
                programme_id=prog.id, year_level=current_year,
                group_label=label, intake_size=current_size or 30, parent_id=None)
            db.session.add(sg)
            db.session.flush()
            print(f'  [NEW] StudentGroup: {label} (size={current_size})')
        elif current_size and current_size > 0 and sg.intake_size != current_size:
            sg.intake_size = current_size

        course = Course.query.filter_by(
            module_code=current_module, programme_id=prog.id, trimester=TRIMESTER).first()
        if not course:
            course = Course(
                programme_id=prog.id, module_code=current_module,
                title=current_title or current_module, year_level=current_year,
                trimester=TRIMESTER, delivery_mode=delivery_mode,
                sessions_per_week=1, total_hours=0)
            db.session.add(course)
            db.session.flush()

        sig = (current_module, prog.id, session_type, 'All', teaching_weeks or '')
        if sig in seen:
            print(f'  SKIP (dup): {current_module} {session_type}')
            continue
        seen.add(sig)

        cs = ClassSession(
            course_id=course.id, session_type=session_type,
            delivery_mode=delivery_mode, is_async=is_async,
            duration_hours=duration_hours, student_group_id=sg.id,
            trimester=TRIMESTER, teaching_weeks=teaching_weeks,
            group_label='All', preferred_timeslot_id=None)
        db.session.add(cs)
        db.session.flush()
        created += 1

        staff_cols = [('staff1', 'staff_id1'), ('staff2', 'staff_id2'), ('staff3', 'staff_id3')]
        is_first = True
        for sn_col, sid_col in staff_cols:
            sn = row.get(sn_col)
            sid = row.get(sid_col)
            if sn is None and sid is None:
                continue
            prof = get_or_create_professor(sn if sn is not None else '', sid if sid is not None else '', None)
            if not prof:
                continue
            already = ClassSessionProfessor.query.filter_by(
                session_id=cs.id, professor_id=prof.id).first()
            if not already:
                db.session.add(ClassSessionProfessor(
                    session_id=cs.id, professor_id=prof.id,
                    is_primary=is_first, display_order=0 if is_first else 1))
                if is_first:
                    is_first = False

    db.session.commit()
    print(f'\nDone — removed {removed} wrong sessions, created {created} new METS-Y4 sessions.')
