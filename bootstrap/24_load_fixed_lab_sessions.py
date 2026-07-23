"""
STEP 24 — Load fixed lab sessions from Requirements Template_Lab.

For each row in the lab file, finds the most-common day/time slot per
(programme, module_code, group) and sets fixed_timeslot_id on the matching
ClassSession. Creates missing ClassSessions where needed.

Lab file: Requirements Template_Lab (ENG) - AY25 Tri 1.xlsx
Each sheet = one programme (METS, RSE, ASE, etc.)

Run AFTER step 23:
    python bootstrap/24_load_fixed_lab_sessions.py
"""
import sys, os, re, secrets
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from datetime import time as dtime
from app import create_app, db
from app.models.programme import Programme
from app.models.course import Course
from app.models.class_session import ClassSession
from app.models.student_group import StudentGroup
from app.models.timeslot import TimeSlot
from app.models.professor import Professor
from app.models.user import User
from app.models.class_session_professor import ClassSessionProfessor

LAB_FILE = (
    r'C:\Users\brain\Downloads\Data_extracted\Data\Requirements_ENG'
    r'\Requirements Template_Lab (ENG) - AY25 Tri 1.xlsx'
)

SKIP_SHEETS = frozenset({'mod list', 'uni wide mod', 'sample', 'sheet1', 'dsc', 'csm', 'msc'})

DAY_MAP = {
    'mon': 'Monday',    'monday': 'Monday',
    'tue': 'Tuesday',   'tuesday': 'Tuesday',
    'wed': 'Wednesday', 'wednesday': 'Wednesday',
    'thu': 'Thursday',  'thursday': 'Thursday',
    'fri': 'Friday',    'friday': 'Friday',
}

SKIP_NAMES = frozenset({
    'nan', 'john doe', 'jane smith', 'temp staff', 'tbc', 'tbd',
    't1', 't2', 't3', 't4', 't5', 'tba', 'n/a', '-',
})


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
    m = re.match(r'(\d{1,2})(am|pm)', s)
    if m:
        h = int(m.group(1))
        if m.group(2) == 'pm' and h != 12:
            h += 12
        if m.group(2) == 'am' and h == 12:
            h = 0
        try:
            return dtime(h, 0)
        except ValueError:
            return None
    return None


def count_weeks(weeks_raw):
    if pd.isna(weeks_raw):
        return 0
    parts = re.findall(r'\d+', str(weeks_raw))
    return len([p for p in parts if 1 <= int(p) <= 52])


def normalise_weeks(raw):
    if pd.isna(raw):
        return None
    s = str(raw).strip()
    parts = re.findall(r'\d+', s)
    if parts:
        weeks = sorted(set(int(p) for p in parts if 1 <= int(p) <= 52))
        return ','.join(str(w) for w in weeks)
    return None


def find_timeslot(day_str, start_t, duration_h, all_slots):
    if not day_str or not start_t:
        return None
    day = DAY_MAP.get(day_str.lower()[:3])
    if not day:
        return None
    end_h = start_t.hour + duration_h
    if end_h >= 24:
        return None
    end_t = dtime(end_h, start_t.minute)
    for slot in all_slots:
        if (slot.day_of_week == day
                and slot.start_time == start_t
                and slot.end_time == end_t):
            return slot
    candidates = [s for s in all_slots if s.day_of_week == day and s.start_time == start_t]
    if candidates:
        return min(candidates, key=lambda s: abs(
            (s.end_time.hour * 60 + s.end_time.minute)
            - (end_t.hour * 60 + end_t.minute)
        ))
    return None


def sheet_to_prog_code(sheet_name):
    """Map sheet tab name to programme code."""
    MAPPING = {
        'ASE': 'ASE', 'DSC': 'DSC', 'EPE': 'EPE', 'EDE': 'EDE',
        'CVE': 'CVE', 'MEC': 'MEC', 'RSE': 'RSE', 'METS': 'METS',
        'SBE': 'SBE', 'MDME': 'MDME', 'NAME': 'NAME', 'ENG': 'ENG',
        'ESE': 'ESE', 'EEE': 'EEE', 'ISE': 'ISE', 'SDE': 'SDE',
    }
    return MAPPING.get(sheet_name.strip().upper())


def normalise_prog_year(raw):
    if pd.isna(raw):
        return None, None
    s = str(raw).strip().upper()
    s = re.sub(r'\n.*', '', s).strip()
    patterns = [
        r'^([A-Z]+)\s*/\s*(?:YR|Y)\s*(\d)',
        r'^([A-Z]+)\s+(?:YR|YEAR)\s*(\d)',
        r'^([A-Z]+)\s*/\s*(\d)',
    ]
    for pat in patterns:
        m = re.search(pat, s)
        if m:
            return m.group(1).upper(), int(m.group(2))
    return None, None


def parse_lab_sheet(df_raw, prog_code_from_sheet, all_slots):
    """
    Parse one lab sheet. Yields record dicts.
    Returns: dict keyed by (module_code, group, prog_code, year_level) with list of slot candidates.
    """
    # Find header row
    header_row_idx = None
    for i, row in df_raw.iterrows():
        vals = [str(v).strip().lower() for v in row if not pd.isna(v) and str(v).strip()]
        if 'module code' in vals and 'day' in vals:
            header_row_idx = i
            break
    if header_row_idx is None:
        return []

    df = df_raw.iloc[header_row_idx + 1:].copy()
    df.columns = df_raw.iloc[header_row_idx].tolist()
    df = df.dropna(how='all')

    # Normalise column names
    col_map = {}
    for c in df.columns:
        lc = str(c).strip().lower()
        if 'prog' in lc:              col_map[c] = 'prog_yr'
        elif 'module code' in lc:     col_map[c] = 'module_code'
        elif 'group' in lc and 'size' not in lc: col_map[c] = 'group'
        elif 'group size' in lc:      col_map[c] = 'group_size'
        elif lc == 'day':             col_map[c] = 'day'
        elif 'start time' in lc:      col_map[c] = 'start_time'
        elif 'duration' in lc:        col_map[c] = 'duration'
        elif 'week' in lc and 'start' not in lc: col_map[c] = 'weeks'
        elif lc == 'staff 1':         col_map[c] = 'staff1'
        elif 'staff id 1' in lc:      col_map[c] = 'staff_id1'
    df = df.rename(columns=col_map)

    def cv(row, name):
        v = row.get(name)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return str(v).strip() if v != '' else None

    current_prog = None
    current_year = None
    current_module = None
    current_group = None
    current_size = None

    # Group rows by (module_code, group) → list of (timeslot, weeks_count, week_list)
    candidates = {}  # key → list of (slot, week_count, week_str)

    for _, row in df.iterrows():
        row = row.to_dict()

        py_raw = row.get('prog_yr')
        if py_raw is not None and not pd.isna(py_raw) and str(py_raw).strip():
            v = str(py_raw).strip()
            if v.lower() not in ('nan', 'prog/yr'):
                pc, yl = normalise_prog_year(v)
                if pc:
                    current_prog = pc
                if yl:
                    current_year = yl
                gs_raw = row.get('group_size')
                if gs_raw is not None and not pd.isna(gs_raw):
                    try:
                        current_size = int(float(str(gs_raw)))
                    except (ValueError, TypeError):
                        pass

        mc_raw = row.get('module_code')
        if mc_raw is not None and not pd.isna(mc_raw) and str(mc_raw).strip():
            v = str(mc_raw).strip().upper()
            if v.lower() not in ('nan', 'module code'):
                # Extract just the module code (e.g. "RSE2108" from "RSE2108 – LECTURE & LAB")
                m_code = re.match(r'^([A-Z]{2,6}\d{3,6}[A-Z]?)', v)
                if m_code:
                    current_module = m_code.group(1)
                else:
                    current_module = v.split('/')[0].strip()[:20]

        gp_raw = row.get('group')
        if gp_raw is not None and not pd.isna(gp_raw) and str(gp_raw).strip():
            v = str(gp_raw).strip()
            if v.lower() not in ('nan', 'group'):
                current_group = v

        if not current_module:
            continue

        day_raw    = cv(row, 'day')
        start_raw  = cv(row, 'start_time')
        dur_raw    = cv(row, 'duration')
        weeks_raw  = cv(row, 'weeks')

        if not day_raw or not start_raw:
            continue

        start_t = parse_time_str(start_raw)
        if not start_t:
            continue

        try:
            duration_h = int(float(str(dur_raw))) if dur_raw else 3
        except (ValueError, TypeError):
            duration_h = 3

        slot = find_timeslot(day_raw.lower()[:3], start_t, duration_h, all_slots)
        if not slot:
            continue

        week_count = count_weeks(weeks_raw)
        week_str   = normalise_weeks(weeks_raw)

        # Infer prog/year from module code if missing
        used_prog = current_prog or prog_code_from_sheet
        if not current_year and current_module:
            m = re.search(r'(\d)', current_module)
            if m and 1 <= int(m.group(1)) <= 4:
                used_year = int(m.group(1))
            else:
                used_year = None
        else:
            used_year = current_year

        if not used_prog or not used_year:
            continue

        group_lbl = (current_group or 'P1')[:20]
        key = (current_module, group_lbl, used_prog, used_year, current_size)
        if key not in candidates:
            candidates[key] = []
        candidates[key].append((slot, week_count, week_str))

    return candidates


def get_or_create_professor_lab(name_raw, sid_raw):
    name = str(name_raw).strip() if not pd.isna(name_raw) else ''
    sid  = str(sid_raw).strip()  if not pd.isna(sid_raw)  else ''

    if not name or name.lower().strip() in SKIP_NAMES:
        return None
    if re.fullmatch(r't\d{1,2}', name.lower().strip()):
        return None

    name = name.title()
    sid  = re.sub(r'[^\w]', '', sid)
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
            return prof

    if not sid:
        base = abs(hash(name)) % 9999
        sid  = f'ENG{base:04d}'
        att  = 0
        while Professor.query.filter_by(staff_id=sid).first():
            att += 1
            sid = f'ENG{(base + att) % 9999:04d}'

    email_local = re.sub(r'[^a-z0-9]', '.', name.lower()).strip('.')
    email_local = re.sub(r'\.+', '.', email_local)
    email = f'{email_local}@sit.edu.sg'
    att = 0
    while User.query.filter_by(email=email).first():
        att += 1
        email = f'{email_local}.{att}@sit.edu.sg'

    user = User(name=name, email=email, role='professor')
    user.set_password(secrets.token_urlsafe(24))
    db.session.add(user)
    db.session.flush()
    prof = Professor(user_id=user.id, staff_id=sid, department='ENG')
    db.session.add(prof)
    db.session.flush()
    return prof


app = create_app()
with app.app_context():
    all_slots = TimeSlot.query.all()
    xl = pd.ExcelFile(LAB_FILE)

    fixed_count   = 0
    created_count = 0
    warn_count    = 0

    for sheet_name in xl.sheet_names:
        if sheet_name.strip().lower() in SKIP_SHEETS:
            continue

        prog_code_hint = sheet_to_prog_code(sheet_name)
        try:
            df_raw = pd.read_excel(LAB_FILE, sheet_name=sheet_name, header=None)
        except Exception as e:
            print(f'  [WARN] Cannot read sheet {sheet_name}: {e}')
            continue

        candidates = parse_lab_sheet(df_raw, prog_code_hint, all_slots)
        if not candidates:
            print(f'  [SKIP] sheet {sheet_name}: no fixed sessions parsed')
            continue

        sheet_fixed = 0
        for (module_code, group_label, prog_code, year_level, group_size), slot_list in candidates.items():
            # Pick the slot with the most weeks (most recurring)
            slot_list.sort(key=lambda x: -x[1])
            best_slot, _, week_str = slot_list[0]

            prog = Programme.query.filter_by(code=prog_code).first()
            if not prog:
                print(f'  [WARN] Programme {prog_code} not found, skipping {module_code}')
                warn_count += 1
                continue

            # Find matching ClassSession by course + session_type='lab' + group_label
            course = Course.query.filter_by(
                module_code=module_code, programme_id=prog.id, trimester=1
            ).first()

            cs = None
            if course:
                cs = ClassSession.query.filter_by(
                    course_id=course.id,
                    session_type='lab',
                    group_label=group_label,
                ).first()
                if not cs:
                    # Try without group match
                    cs = ClassSession.query.filter_by(
                        course_id=course.id,
                        session_type='lab',
                    ).first()

            if cs:
                cs.fixed_timeslot_id = best_slot.id
                if week_str:
                    cs.teaching_weeks = week_str
                fixed_count += 1
                sheet_fixed += 1
            else:
                # Create course + ClassSession if missing
                if not course:
                    sg = StudentGroup.query.filter_by(
                        programme_id=prog.id, year_level=year_level, parent_id=None
                    ).first()
                    if not sg:
                        sg = StudentGroup(
                            programme_id=prog.id,
                            year_level=year_level,
                            group_label=f'{prog_code}-Y{year_level}',
                            intake_size=group_size or 30,
                            parent_id=None,
                        )
                        db.session.add(sg)
                        db.session.flush()

                    course = Course(
                        programme_id=prog.id,
                        module_code=module_code,
                        title=module_code,
                        year_level=year_level,
                        trimester=1,
                        delivery_mode='f2f',
                        sessions_per_week=1,
                        total_hours=0,
                    )
                    db.session.add(course)
                    db.session.flush()

                sg = StudentGroup.query.filter_by(
                    programme_id=prog.id, year_level=year_level, parent_id=None
                ).first()

                cs = ClassSession(
                    course_id=course.id,
                    session_type='lab',
                    delivery_mode='f2f',
                    is_async=False,
                    duration_hours=best_slot.end_time.hour - best_slot.start_time.hour,
                    student_group_id=sg.id if sg else None,
                    trimester=1,
                    teaching_weeks=week_str,
                    group_label=group_label,
                    fixed_timeslot_id=best_slot.id,
                )
                db.session.add(cs)
                db.session.flush()
                created_count += 1
                sheet_fixed += 1

        db.session.commit()
        print(f'  [OK] sheet {sheet_name}: {sheet_fixed} sessions fixed/created')

    print(f'\nFixed lab sessions complete:')
    print(f'  Updated existing sessions:  {fixed_count}')
    print(f'  Created new lab sessions:   {created_count}')
    print(f'  Warnings:                   {warn_count}')
