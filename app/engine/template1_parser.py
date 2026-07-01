"""
Shared parser for SIT Template 1 Excel files (ENG cluster Requirements Template format).

Column layout (keyword-detected, order-insensitive):
  Prog/Yr | Class Size | Module Code | Module Name | Activity | Delivery Mode |
  Teaching Weeks | Staff 1 | Staff ID 1 | Staff 2 | Staff ID 2 | Remarks

Returns parsed session dicts via load_module_sheet(); DB writes happen in the caller.
"""
import re
import pandas as pd
from datetime import time as dtime

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

SKIP_ACTIVITIES = frozenset({
    'practicum', 'field trip', 'fieldwork', 'attachment', 'internship',
})

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


# ---------------------------------------------------------------------------
# Pure parsing helpers (no DB access)
# ---------------------------------------------------------------------------

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
    Handles: "Day: Monday\nTime: 9 am to 11 am\nVenue: ..."
             "Group A\nDay: Friday\nTime: 9 am to 12 pm\nVenue: ..."
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


def prog_from_filename(fname):
    """Extract (prog_code, year_or_None) from filename as a fallback."""
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
    """
    if pd.isna(raw):
        return None, None
    s = str(raw).strip()
    s = re.sub(r'\n.*', '', s).strip()
    s = re.sub(r'\s+and\s+\w+\s+(?:cbe|common)?\b', '', s, flags=re.IGNORECASE).strip()
    s_up = s.upper()

    patterns = [
        r'^([A-Z]+)\s*/\s*(?:YR|Y)\s*(\d)',
        r'^([A-Z]+)\s+(?:YR|YEAR)\s*(\d)',
        r'^([A-Z]+)\s*/\s*(?:YEAR)\s*(\d)',
        r'^([A-Z]+)\s*/\s*(\d)',
        r'^([A-Z]+)\s+(?:CBE|COMMON).*?(?:YR|YEAR|Y)\s*(\d)',
    ]
    for pat in patterns:
        m = re.search(pat, s_up)
        if m:
            return m.group(1).upper(), int(m.group(2))
    return None, None


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
        elif 'teaching weeks' in lc:
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
    Parse a Module sheet (Template 1 format). Yields session dicts.
    fname_hint is the source filename used to infer the programme code
    when the Prog/Yr cell has an unusual format.

    Each yielded dict has keys:
      prog_code, year_level, class_size, module_code, module_title,
      session_type, delivery_mode, is_async, duration_hours,
      teaching_weeks, group_label, pref_slot_id,
      staff: [(name, sid), ...]
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

        if not current_year and current_module:
            current_year = year_from_module_code(current_module)

        if not current_prog or not current_year:
            continue

        dm_raw = row.get('delivery_mode')
        dm_str = (str(dm_raw).strip().lower()
                  if dm_raw is not None and not pd.isna(dm_raw) else 'f2f')
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
            'staff': [
                (row.get('staff1'),  row.get('staff_id1')),
                (row.get('staff2'),  row.get('staff_id2')),
                (row.get('staff3'),  row.get('staff_id3')),
            ],
        }
