"""
STEP 23 — Load all ENG cluster programme data from Template 1 Excel files.

All files in Data/Requirements_ENG/ are AY25 Tri 1 (2510) files.
Each file covers one (or a combined) programme year.
The single 'Module' sheet contains all sessions.

Run AFTER step 22:
    python bootstrap/23_load_eng_programmes.py

Data directory: C:/Users/brain/Downloads/Data_extracted/Data/Requirements_ENG/
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
from app.models.professor import Professor
from app.models.user import User
from app.models.timeslot import TimeSlot
from app.models.class_session_professor import ClassSessionProfessor

DATA_DIR = r'C:\Users\brain\Downloads\Data_extracted\Data\Requirements_ENG'

TRIMESTER = 1  # All 2510 files are Tri 1

SESSION_TYPE_MAP = {
    'lecture':    'lecture',
    'lectorial':  'lectorial',
    'tutorial':   'tutorial',
    'lab':        'lab',
    'laboratory': 'lab',
    'workshop':   'workshop',
    'quiz':       'quiz',
    'seminar':    'seminar',
}

# Activity types that have no weekly timeslot — skip entirely
SKIP_ACTIVITIES = frozenset({'practicum', 'field trip', 'fieldwork', 'attachment', 'internship'})

DELIVERY_MAP = {
    'f2f':                    ('f2f',    False),
    'online - synchronous':   ('online', False),
    'online-synchronous':     ('online', False),
    'online synchronous':     ('online', False),
    'online - asynchronous':  ('online', True),
    'online-asynchronous':    ('online', True),
    'online asynchronous':    ('online', True),
    'online':                 ('online', False),
    'hybrid':                 ('f2f',    False),
}

DAY_MAP = {
    'mon': 'Monday',    'monday': 'Monday',
    'tue': 'Tuesday',   'tuesday': 'Tuesday',
    'wed': 'Wednesday', 'wednesday': 'Wednesday',
    'thu': 'Thursday',  'thursday': 'Thursday',
    'fri': 'Friday',    'friday': 'Friday',
}

DURATION_DEFAULTS = {
    'lecture': 2, 'lectorial': 2, 'tutorial': 2,
    'lab': 3, 'workshop': 3, 'quiz': 2, 'seminar': 2,
}

PROG_NAMES = {
    'ASE':  'Aerospace Systems Engineering',
    'CVE':  'Civil Engineering',
    'EDE':  'Engineering Design',
    'EEE':  'Electrical and Electronic Engineering',
    'EPE':  'Electrical Power Engineering',
    'ESE':  'Engineering Systems and Environment',
    'ISE':  'Industrial Systems Engineering',
    'MDME': 'Manufacturing, Design and Mechanical Engineering',
    'MEC':  'Mechanical Engineering',
    'METS': 'Mechatronics Systems',
    'NAME': 'Naval Architecture and Marine Engineering',
    'RSE':  'Robotics Systems Engineering',
    'SBE':  'Sustainable Built Environment',
    'SDE':  'Sustainable Design Engineering',
    'ENG':  'Common Engineering',
    'DSC':  'Data Science and Computing',
}

SKIP_NAMES = frozenset({
    'nan', 'john doe', 'jane smith', 'temp staff', 'tbc', 'tbd',
    't1', 't2', 't3', 't4', 't5', 'tba', 'n/a', '-',
})

SKIP_SHEETS = frozenset({
    'sample', 'standard period block', 'sheet1', 'sheet2', 'sheet3',
    'groupings', 'mod list', 'uni wide mod', 'ase', 'dsc', 'epe',
    'ede', 'cve', 'mec', 'msc', 'rse', 'mets', 'sbe', 'mdme',
    'name', 'eng', 'csm', 'ese', 'eee', 'ise',
})

SKIP_FILES = {
    'Requirements Template_Lab (ENG) - AY25 Tri 1.xlsx',
    'Requirements Template_ENG.xlsx',
    '2510_DSC.xlsx',
}


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


def find_timeslot(day_str, start_t, duration_hours, all_slots):
    if not day_str or not start_t:
        return None
    day = DAY_MAP.get(day_str.lower()[:3])
    if not day:
        return None
    end_h = start_t.hour + duration_hours
    if end_h >= 24:
        return None
    end_t = dtime(end_h, start_t.minute)
    for slot in all_slots:
        if (slot.day_of_week == day
                and slot.start_time == start_t
                and slot.end_time == end_t):
            return slot
    candidates = [s for s in all_slots
                  if s.day_of_week == day and s.start_time == start_t]
    if candidates:
        return min(candidates, key=lambda s: abs(
            (s.end_time.hour * 60 + s.end_time.minute)
            - (end_t.hour * 60 + end_t.minute)
        ))
    return None


def parse_remarks(remarks_str, duration_hours, all_slots):
    """
    Extract (preferred TimeSlot or None, group_label str or None) from a Remarks cell.
    Handles: "Day: Monday\\nTime: 9 am to 11 am\\nVenue: ..."
             "Group A\\nDay: Friday\\nTime: 9 am to 12 pm\\nVenue: ..."
    """
    if pd.isna(remarks_str) or not str(remarks_str).strip():
        return None, None
    r = str(remarks_str)

    group_label = None
    gm = re.search(r'\bGroup\s+([A-Z0-9]+)', r, re.IGNORECASE)
    if gm:
        group_label = gm.group(1).upper()
    elif re.search(r'\bAll\b', r, re.IGNORECASE):
        group_label = 'All'

    dm = re.search(r'Day:\s*([A-Za-z]+)', r, re.IGNORECASE)
    if not dm:
        return None, group_label

    tm = re.search(r'Time:\s*([\d:]+\s*(?:am|pm)?)', r, re.IGNORECASE)
    if not tm:
        return None, group_label

    start_t = parse_time_str(tm.group(1).strip())
    if not start_t:
        return None, group_label

    slot = find_timeslot(dm.group(1).strip().lower()[:3], start_t, duration_hours, all_slots)
    return slot, group_label


def normalise_weeks(raw):
    """Parse a Teaching Weeks cell into a comma-separated week-number string.

    Some source rows (one-off/block-teaching sessions) pack a "Week N" label
    and a full date/time note into the same cell, e.g.
    "Week 3\\nWed, 17 Sep: 2pm - 4pm (2.5hrs)". Only the first line carries
    the actual week(s); everything after is a human-readable date/time note
    whose numbers (day-of-month, times, durations) must NOT be treated as
    week numbers. When the cell leads with "Week"/"Weeks", only that leading
    token is parsed (supports "Week 5", "Week 5-6", "Week 8-13, 15"); ranges
    are expanded. Cells without a "Week" prefix (plain lists like
    "1,2,3,...,13") fall back to extracting every number on the first line.
    """
    if pd.isna(raw):
        return None
    s = str(raw).strip()
    if re.match(r'\d{4}-\d{2}-\d{2}', s):
        return None
    first_line = s.split('\n')[0].strip()

    m = re.match(
        r'^weeks?\b\s*[:\-]?\s*'
        r'((?:\d+\s*(?:-\s*\d+)?)(?:[,\s]+\d+\s*(?:-\s*\d+)?)*)',
        first_line, re.IGNORECASE,
    )
    if m:
        weeks = set()
        for part in re.split(r'[,\s]+', m.group(1).strip()):
            rm = re.match(r'^(\d+)\s*-\s*(\d+)$', part)
            if rm:
                a, b = int(rm.group(1)), int(rm.group(2))
                weeks.update(range(min(a, b), max(a, b) + 1))
            elif part.isdigit():
                weeks.add(int(part))
        weeks = {w for w in weeks if 1 <= w <= 52}
        return ','.join(str(w) for w in sorted(weeks)) if weeks else None

    parts = re.findall(r'\d+', first_line)
    if parts:
        weeks = sorted(set(int(p) for p in parts if 1 <= int(p) <= 52))
        return ','.join(str(w) for w in weeks)
    return None


def prog_from_filename(fname):
    """Extract (prog_code, year_or_None) from filename as fallback."""
    stem = re.sub(r'\.(xlsx?)$', '', fname, flags=re.IGNORECASE)
    year_m = re.search(r'\byear\s*(\d)\b', stem, re.IGNORECASE)
    year = int(year_m.group(1)) if year_m else None
    found = []
    for code in PROG_NAMES:
        m = re.search(r'\b' + code + r'\b', stem, re.IGNORECASE)
        if m:
            found.append((m.start(), code.upper()))
    if found:
        found.sort()
        return found[0][1], year
    return None, year


def year_from_module_code(module_code):
    """Infer year level from first digit in module code, e.g. SDE3001 → 3."""
    m = re.search(r'(\d)', module_code or '')
    if m:
        d = int(m.group(1))
        if 1 <= d <= 4:
            return d
    return None


def normalise_prog_year(raw):
    """
    Parse (prog_code, year_level) from Prog/Yr field.
    Handles: 'ASE YR 1', 'METS/Y2', 'EEE and ISE CBE/Yr 1', 'SBE Yr 1' etc.
    Also handles intake-year format: 'METS/2022' → METS Year 4 (AY2526: 2026 - intake_year).
    """
    if pd.isna(raw):
        return None, None
    s = str(raw).strip()
    s = re.sub(r'\n.*', '', s).strip()
    # Strip "and PROG CBE" for combined-class rows like "EEE and ISE CBE/Yr 1"
    s = re.sub(r'\s+and\s+\w+\s+(?:cbe|common)?\b', '', s, flags=re.IGNORECASE).strip()
    s_up = s.upper()

    # Handle intake-year format "PROG/YYYY" before single-digit patterns to avoid
    # misreading "METS/2022" as year 2 (first digit of 2022).
    intake_m = re.search(r'^([A-Z]+)\s*/\s*(20\d{2})\b', s_up)
    if intake_m:
        intake_year = int(intake_m.group(2))
        year_level = 2026 - intake_year  # AY2526: 2022→Y4, 2023→Y3, 2024→Y2, 2025→Y1
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


def get_or_create_programme(code):
    prog = Programme.query.filter_by(code=code).first()
    if not prog:
        prog = Programme(
            code=code,
            name=PROG_NAMES.get(code, code),
            cluster='ENG',
        )
        db.session.add(prog)
        db.session.flush()
        print(f'  [NEW] Programme: {code}')
    return prog


def get_or_create_student_group(prog, year_level, intake_size):
    label = f'{prog.code}-Y{year_level}'
    sg = StudentGroup.query.filter_by(
        programme_id=prog.id,
        year_level=year_level,
        group_label=label,
        parent_id=None,
    ).first()
    if not sg:
        sg = StudentGroup(
            programme_id=prog.id,
            year_level=year_level,
            group_label=label,
            intake_size=intake_size or 30,
            parent_id=None,
        )
        db.session.add(sg)
        db.session.flush()
        print(f'  [NEW] StudentGroup: {label} (size={intake_size})')
    elif intake_size and intake_size > 0 and sg.intake_size != intake_size:
        sg.intake_size = intake_size
    return sg


def get_or_create_professor(name_raw, sid_raw):
    name = str(name_raw).strip() if not pd.isna(name_raw) else ''
    sid  = str(sid_raw).strip()  if not pd.isna(sid_raw)  else ''

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
    user.set_password(secrets.token_urlsafe(24))
    db.session.add(user)
    db.session.flush()
    prof = Professor(user_id=user.id, staff_id=sid, department='ENG')
    db.session.add(prof)
    db.session.flush()
    return prof


def get_or_create_course(module_code, title, prog, year_level, delivery_mode):
    course = Course.query.filter_by(
        module_code=module_code,
        programme_id=prog.id,
        trimester=TRIMESTER,
    ).first()
    if not course:
        course = Course(
            programme_id=prog.id,
            module_code=module_code,
            title=title or module_code,
            year_level=year_level,
            trimester=TRIMESTER,
            delivery_mode=delivery_mode,
            sessions_per_week=1,
            total_hours=0,
        )
        db.session.add(course)
        db.session.flush()
    return course


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
        elif lc == 'staff 3':
            col_map[c] = 'staff3'
        elif 'staff id 3' in lc:
            col_map[c] = 'staff_id3'
        elif 'remark' in lc or 'note' in lc:
            col_map[c] = 'remarks'
    return col_map


def load_module_sheet(df_raw, all_slots, fname_hint=None):
    """
    Parse a Module sheet. fname_hint is the source filename used to infer
    the programme code when the Prog/Yr cell has an unusual format.
    """
    header_row_idx = None
    for i, row in df_raw.iterrows():
        vals = [str(v).strip().lower() for v in row if not pd.isna(v) and str(v).strip()]
        if 'prog/yr' in vals or ('module code' in vals and 'activity' in vals):
            header_row_idx = i
            break
    if header_row_idx is None:
        return

    col_map = build_col_map(df_raw.iloc[header_row_idx].tolist())
    df = df_raw.iloc[header_row_idx + 1:].copy()
    df.columns = df_raw.iloc[header_row_idx].tolist()
    df = df.rename(columns=col_map)
    df = df.dropna(how='all')

    # Derive fallback prog/year from filename
    fn_prog, fn_year = prog_from_filename(fname_hint) if fname_hint else (None, None)

    def col(row, name, default=None):
        v = row.get(name, default)
        if pd.isna(v) if v is not None else False:
            return default
        return v

    current_prog   = fn_prog
    current_year   = fn_year
    current_size   = None
    current_module = None
    current_title  = None

    for _, row in df.iterrows():
        row = row.to_dict()

        py_raw = row.get('prog_yr')
        if py_raw is not None and not pd.isna(py_raw) and str(py_raw).strip():
            v = str(py_raw).strip()
            if v.lower() not in ('nan', 'prog/yr'):
                pc, yl = normalise_prog_year(v)
                if pc and yl:
                    current_prog = pc
                    current_year = yl
                elif pc:
                    current_prog = pc
                # Even if parsing failed, update class size from this row
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
                # Handle combined codes like "MET4004/SEM4007" — use primary
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

        # Infer year from module code if still unknown
        if not current_year and current_module:
            current_year = year_from_module_code(current_module)

        if not current_prog or not current_year:
            continue

        dm_raw = row.get('delivery_mode')
        dm_str = str(dm_raw).strip().lower() if dm_raw is not None and not pd.isna(dm_raw) else 'f2f'
        delivery_mode, is_async = DELIVERY_MAP.get(dm_str, ('f2f', False))

        teaching_weeks = normalise_weeks(row.get('teaching_weeks'))
        remarks_raw    = row.get('remarks')
        duration_hours = DURATION_DEFAULTS.get(session_type, 2)

        pref_slot, group_hint = parse_remarks(remarks_raw, duration_hours, all_slots)
        group_label = group_hint or 'All'

        yield {
            'prog_code':      current_prog,
            'year_level':     current_year,
            'class_size':     current_size,
            'module_code':    current_module,
            'module_title':   current_title,
            'session_type':   session_type,
            'delivery_mode':  delivery_mode,
            'is_async':       is_async,
            'duration_hours': duration_hours,
            'teaching_weeks': teaching_weeks,
            'group_label':    group_label,
            'pref_slot_id':   pref_slot.id if pref_slot else None,
            'staff':          [
                (row.get('staff1'), row.get('staff_id1')),
                (row.get('staff2'), row.get('staff_id2')),
                (row.get('staff3'), row.get('staff_id3')),
            ],
        }


def discover_files():
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith('.xlsx') or fname in SKIP_FILES:
            continue
        fpath = os.path.join(DATA_DIR, fname)
        try:
            xl = pd.ExcelFile(fpath)
        except Exception as e:
            print(f'  [WARN] Cannot open {fname}: {e}')
            continue
        for sheet in xl.sheet_names:
            if sheet.strip().lower() in SKIP_SHEETS:
                continue
            yield fpath, sheet, fname


app = create_app()
with app.app_context():
    all_slots = TimeSlot.query.all()

    created_sessions = 0
    skipped          = 0

    # Pre-populate seen set from DB to skip already-loaded sessions on re-run
    seen = set()
    for cs in ClassSession.query.join(Course).all():
        prog_id = cs.course.programme_id
        seen.add((cs.course.module_code, prog_id, cs.session_type,
                  cs.group_label or 'All', cs.teaching_weeks or ''))
    print(f'  Pre-loaded {len(seen)} existing sessions into dedup cache.')

    for fpath, sheet_name, fname in discover_files():
        try:
            df_raw = pd.read_excel(fpath, sheet_name=sheet_name, header=None)
        except Exception as e:
            print(f'  [WARN] Cannot read {fname}/{sheet_name}: {e}')
            continue

        file_sessions = 0
        for rec in load_module_sheet(df_raw, all_slots, fname_hint=fname):
            prog_code  = rec['prog_code']
            year_level = rec['year_level']
            if not prog_code or not year_level:
                skipped += 1
                continue

            prog   = get_or_create_programme(prog_code)
            sg     = get_or_create_student_group(prog, year_level, rec['class_size'])
            course = get_or_create_course(
                rec['module_code'], rec['module_title'],
                prog, year_level, rec['delivery_mode'],
            )

            sig = (rec['module_code'], prog.id, rec['session_type'],
                   rec['group_label'], rec['teaching_weeks'] or '')
            if sig in seen:
                skipped += 1
                continue
            seen.add(sig)

            cs = ClassSession(
                course_id=course.id,
                session_type=rec['session_type'],
                delivery_mode=rec['delivery_mode'],
                is_async=rec['is_async'],
                duration_hours=rec['duration_hours'],
                student_group_id=sg.id,
                trimester=TRIMESTER,
                teaching_weeks=rec['teaching_weeks'],
                group_label=rec['group_label'],
                preferred_timeslot_id=rec['pref_slot_id'],
            )
            db.session.add(cs)
            db.session.flush()
            created_sessions += 1
            file_sessions += 1

            is_first = True
            for staff_name, staff_id in rec['staff']:
                prof = get_or_create_professor(staff_name, staff_id)
                if not prof:
                    continue
                already = ClassSessionProfessor.query.filter_by(
                    session_id=cs.id, professor_id=prof.id).first()
                if not already:
                    db.session.add(ClassSessionProfessor(
                        session_id=cs.id,
                        professor_id=prof.id,
                        is_primary=is_first,
                        display_order=0 if is_first else 1,
                    ))
                    if is_first:
                        is_first = False

        if file_sessions:
            db.session.commit()
            print(f'  [OK] {fname} / {sheet_name}: {file_sessions} sessions')
        else:
            db.session.rollback()
            print(f'  [SKIP] {fname} / {sheet_name}: no sessions parsed')

    print(f'\nLoad complete: {created_sessions} sessions created, {skipped} skipped.')
