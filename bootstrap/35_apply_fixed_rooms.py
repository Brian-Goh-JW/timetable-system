"""
STEP 35 — Apply room-locking from the cleaned files' Venue column.

Some cleaned files (CVE, METS, ISE, part of EEE) name an exact room per
session, e.g. "Fabrication Lab E6-07-11" - a room code embedded in a
descriptive label, not an exact Room.room_code match. Extracts the
"E#-##-##"-style code from that text and matches it against existing Room
records. Only sets ClassSession.fixed_room_id when a confident match is
found; unmatched venue text is logged, not guessed at.

Only affects sessions already loaded by bootstrap/32 (Tri 1) and
bootstrap/33 (Tri 2/3) - re-reads the same source files and matches back to
existing sessions by (course, session_type, teaching_weeks, group_label).

Run AFTER bootstrap/33:
    python bootstrap/35_apply_fixed_rooms.py
"""
import sys, os, re
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from app import create_app, db
from app.models.programme import Programme
from app.models.course import Course
from app.models.room import Room
from app.engine.template1_parser import build_col_map, SKIP_SHEETS

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

ROOM_CODE_RE = re.compile(r'([A-Z]\d[-\s]?\d{2}[-\s]?\d{2})')


def extract_room_code(venue_text, all_room_codes):
    if not isinstance(venue_text, str):
        return None
    m = ROOM_CODE_RE.search(venue_text.upper())
    if not m:
        return None
    candidate = re.sub(r'\s', '', m.group(1))
    # normalise to "E6-07-11" form
    m2 = re.match(r'([A-Z])(\d)-?(\d{2})-?(\d{2})', candidate)
    if not m2:
        return None
    normalised = f'{m2.group(1)}{m2.group(2)}-{m2.group(3)}-{m2.group(4)}'
    return normalised if normalised in all_room_codes else None


app = create_app()
with app.app_context():
    all_room_codes = {r.room_code: r.id for r in Room.query.all()}

    applied = 0
    unmatched = []
    checked = 0

    for prog_code, fname in FILES.items():
        fpath = os.path.join(BASE, fname)
        xl = pd.ExcelFile(fpath)
        prog = Programme.query.filter_by(code=prog_code).first()
        if not prog:
            continue

        for trimester, pattern in [(1, 'Tri 1'), (2, 'Tri 2'), (3, 'Tri 3')]:
            sheet_matches = [s for s in xl.sheet_names if pattern in s]
            if not sheet_matches:
                continue
            df_raw = pd.read_excel(fpath, sheet_name=sheet_matches[0], header=None)

            hdr_idx = None
            for i, row in df_raw.iterrows():
                vals = [str(v).strip().lower() for v in row if pd.notna(v)]
                if 'prog/yr' in vals or ('module code' in vals and 'activity' in vals):
                    hdr_idx = i
                    break
            if hdr_idx is None:
                continue
            header_vals = df_raw.iloc[hdr_idx].tolist()
            col_map = build_col_map(header_vals)
            if 'venue' not in [str(v).strip().lower() for v in header_vals]:
                continue  # this file/sheet has no Venue column at all

            venue_col = next(c for c in header_vals if str(c).strip().lower() == 'venue')
            body = df_raw.iloc[hdr_idx + 1:].copy()
            body.columns = header_vals

            current_module = None
            course_session_seen = {}
            for _, row in body.iterrows():
                mc = row.get('Module Code')
                if pd.notna(mc) and str(mc).strip():
                    current_module = str(mc).strip().upper().split('/')[0]
                venue_text = row.get(venue_col)
                act = row.get('Activity')
                if pd.isna(act) or not current_module or pd.isna(venue_text):
                    continue
                checked += 1

                room_code = extract_room_code(venue_text, all_room_codes)
                if not room_code:
                    unmatched.append((prog_code, current_module, str(venue_text)[:40]))
                    continue

                course = Course.query.filter_by(module_code=current_module, programme_id=prog.id,
                                                  trimester=trimester).first()
                if not course:
                    continue
                act_str = str(act).strip().lower()
                from app.engine.template1_parser import SESSION_TYPE_MAP
                session_type = SESSION_TYPE_MAP.get(act_str)
                if not session_type:
                    continue
                seen_key = (course.id, session_type)
                idx = course_session_seen.get(seen_key, 0)
                course_session_seen[seen_key] = idx + 1
                matching_sessions = sorted(
                    [s for s in course.class_sessions if s.session_type == session_type],
                    key=lambda s: s.id,
                )
                if idx >= len(matching_sessions):
                    continue
                target = matching_sessions[idx]
                if target.fixed_room_id is None:
                    target.fixed_room_id = all_room_codes[room_code]
                    applied += 1

    db.session.commit()
    print(f'Checked {checked} venue cells, applied {applied} room locks.')
    print(f'\nUnmatched venue text ({len(unmatched)} total, showing distinct):')
    seen_texts = set()
    for prog, mod, txt in unmatched:
        if txt not in seen_texts:
            seen_texts.add(txt)
            print(f'  {prog:6} {mod:10} {txt!r}')
