"""
Bootstrap 18 — Update professors, rooms, and backbone entries from real Excel files.

Sources (authoritative — provided by professor):
    T1: 2510 DSC Year 1-2 Timetable.xlsx   → 'Sheet' tab
    T2: 2520 DSC Year 1-3 Timetable 1.xlsx → 'raw' tab
    T3: 2530 DSC Year 1-2 Timetable 1.xlsx → 'Year 1' + 'Year 2' grid tabs

What changes:
    1. Creates real professor accounts for every professor found in the files.
    2. For every module+session_type covered by the files: clears assumed
       professor links and replaces with real professors from the Excel.
    3. Adds any room codes found in the files that are not already in the DB.
    4. Updates backbone TimetableEntry.room_id for the affected modules to
       the most frequently assigned room in the Excel for that session.

What is NOT touched:
    - Timeslots, student groups, programmes
    - Generated (is_backbone=False) timetable entries
    - Availability declarations
    - Modules not covered by these 3 files
    - Admin / Brian Goh user accounts

Safe to re-run (professors and rooms are get-or-create).
"""

import sys, os, re, secrets
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from collections import Counter, defaultdict
from datetime import time as dtime

from app import create_app, db
from app.models.user import User
from app.models.professor import Professor
from app.models.course import Course
from app.models.class_session import ClassSession
from app.models.class_session_professor import ClassSessionProfessor
from app.models.room import Room
from app.models.timetable_entry import TimetableEntry
from app.models.timeslot import TimeSlot

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------
T1_FILE = r"C:\Users\brain\Downloads\2510 DSC Year 1-2 Timetable.xlsx"
T2_FILE = r"C:\Users\brain\Downloads\2520 DSC Year 1-3 Timetable 1.xlsx"
T3_FILE = r"C:\Users\brain\Downloads\2530 DSC Year 1-2 Timetable 1.xlsx"

AY = 'AY2526'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MOD_RE = re.compile(r'^([A-Z]{2,4}\d{4})-\d{4}-')
ROOM_CODE_RE = re.compile(r'^[A-Z]\d')   # starts with letter+digit → room code

ACTIVITY_MAP = {
    'lecture':    'lecture',
    'lectorial':  'lecture',
    'tutorial':   'tutorial',
    'laboratory': 'lab',
    'lab':        'lab',
    'sr':         'lab',
    'seminar':    'seminar',
    'workshop':   'seminar',
}


def _session_type(raw):
    return ACTIVITY_MAP.get(str(raw).strip().lower(), 'lecture')


def _split_prof_names(raw):
    """Split 'A,B, SUFFIX' into professor name strings.
    Single-word segments that look like a given-name suffix are merged
    with the preceding name (e.g. 'WONG HENG LOONG, NICHOLAS' stays whole).
    """
    raw = str(raw).strip().lstrip('*').strip()
    if not raw or raw.lower() in ('nan', 'none', ''):
        return []
    parts = [p.strip() for p in raw.split(',')]
    names = []
    for part in parts:
        if not part or part.lower() in ('nan', 'none'):
            continue
        # Single-word ALL-CAPS after a previous entry → likely a given name suffix
        if names and len(part.split()) == 1 and part.isupper():
            names[-1] = names[-1] + ', ' + part
        else:
            names.append(part)
    return [n for n in names if n and len(n) >= 2]


def _normalise_name(raw):
    """Title-case a professor name, keeping parenthetical intact."""
    n = str(raw).strip()
    if not n or n.lower() in ('nan', 'none'):
        return None
    # Preserve parenthetical: "AW KOK SENG (HU GUOCHENG)" → "Aw Kok Seng (Hu Guocheng)"
    def _tc(s):
        return ' '.join(w.capitalize() for w in s.split())
    if '(' in n:
        outer, inner = n.split('(', 1)
        inner = inner.rstrip(')')
        return f"{_tc(outer.strip())} ({_tc(inner.strip())})"
    return _tc(n)


def _make_staff_id(name):
    """surname_initials style: 'Lin Weidong' → 'linw', 'Teo Ching Leong' → 'teoc'."""
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][:6] + parts[1][0]).lower().replace(' ', '')
    return parts[0][:7].lower()


def _parse_room(raw):
    """Return the first valid room code from a raw room string (may be comma-joined)."""
    if not raw or str(raw).strip().lower() in ('online', 'nan', '', '*temp venue', 'none'):
        return None
    first = str(raw).split(',')[0].strip()
    # Must start with letter+digit to be a room code
    if first and ROOM_CODE_RE.match(first):
        return first
    return None


def _room_type(code):
    if not code:
        return 'seminar'
    c = code.lower()
    if 'lectorial' in c:
        return 'lectorial'
    # E2-0x-xx without 'sr' suffix → likely lab bench room
    if re.match(r'^[ew]\d-\d{2}-\d{2}$', c):
        return 'lab'
    return 'seminar'


def _room_capacity(rtype):
    return {'lectorial': 200, 'lab': 30, 'seminar': 40}.get(rtype, 40)


# ---------------------------------------------------------------------------
# Phase 1 — Parse Excel files into a flat list of session records
# ---------------------------------------------------------------------------

def parse_structured(filepath, sheet, tri_num):
    """Parse a flat Sheet/raw tab. Returns list of dicts."""
    df = pd.read_excel(filepath, sheet_name=sheet, header=0)
    records = []
    for _, row in df.iterrows():
        name_val = str(row.get('Name', '')).strip()
        m = MOD_RE.match(name_val)
        if not m:
            continue
        module_code = m.group(1)
        stype = _session_type(row.get('Activity Type Name', ''))
        day   = str(row.get('Scheduled Days', '')).strip()
        start = row.get('Scheduled Start Time')
        end   = row.get('Scheduled End Time')
        room  = _parse_room(row.get('Allocated Location Name', ''))
        profs = _split_prof_names(row.get('Allocated Staff Name', ''))
        records.append({
            'module_code': module_code,
            'session_type': stype,
            'day': day,
            'start': start,
            'end': end,
            'room': room,
            'profs': profs,
            'tri': tri_num,
        })
    return records


def parse_grid(filepath, sheet, tri_num):
    """Parse a visual grid tab. Professor names may be truncated."""
    df = pd.read_excel(filepath, sheet_name=sheet, header=None)
    records = []

    # Row 1 → column→day mapping (e.g. col 1 = Monday week 1, col 8 = Monday week 2, ...)
    TIME_RE  = re.compile(r'^(\d{2}):(\d{2})-(\d{2}):(\d{2})$')
    DAY_MAP  = {'Mon': 'Monday', 'Tue': 'Tuesday', 'Wed': 'Wednesday',
                'Thu': 'Thursday', 'Fri': 'Friday'}
    MOD_CELL = re.compile(r'^([A-Z]{2,4}\d{4})\s+(\S+)')

    col_day = {}
    header = df.iloc[1] if df.shape[0] > 1 else pd.Series()
    for ci, val in enumerate(header):
        s = str(val).strip()
        for abbr, full in DAY_MAP.items():
            if s.startswith(abbr + ' '):
                col_day[ci] = full
                break

    seen = set()   # deduplicate (module, stype, day, start) within this sheet
    for ri in range(df.shape[0]):
        row = df.iloc[ri]
        time_raw = str(row.iloc[0]).strip()
        tm = TIME_RE.match(time_raw)
        if not tm:
            continue
        sh, sm, eh, em = int(tm.group(1)), int(tm.group(2)), int(tm.group(3)), int(tm.group(4))
        start_t = dtime(sh, sm)
        end_t   = dtime(eh, em)
        dur_min = (eh * 60 + em) - (sh * 60 + sm)
        inferred_type = 'lab' if dur_min >= 150 else ('lecture' if dur_min >= 90 else 'tutorial')

        for ci, cell_val in enumerate(row):
            if ci not in col_day:
                continue
            cell = str(cell_val).strip()
            if not cell or cell == time_raw or re.match(r'^\d{2}:\d{2}', cell):
                continue

            # Normalise newlines → pipe
            cell_norm = cell.replace('\n', '|')
            parts = [p.strip() for p in cell_norm.split('|') if p.strip()]
            if not parts:
                continue

            mc = MOD_CELL.match(parts[0])
            if not mc:
                continue
            module_code = mc.group(1)
            day = col_day[ci]

            # parts[1] is room or "Online"; parts[2] is prof (often truncated)
            room = None
            prof_raw = None
            if len(parts) >= 2:
                p1 = parts[1]
                if p1.lower() not in ('online',) and ROOM_CODE_RE.match(p1):
                    room = _parse_room(p1)
                elif p1.lower() == 'online':
                    room = None
                # If part 1 doesn't look like room, it might be a time label — skip
            if len(parts) >= 3:
                prof_raw = parts[2]

            key = (module_code, inferred_type, day, sh, sm)
            if key in seen:
                continue
            seen.add(key)

            profs = [prof_raw] if prof_raw and len(prof_raw) >= 2 else []
            records.append({
                'module_code': module_code,
                'session_type': inferred_type,
                'day': day,
                'start': start_t,
                'end': end_t,
                'room': room,
                'profs': profs,
                'tri': tri_num,
            })
    return records


# ---------------------------------------------------------------------------
# Phase 2 — Aggregate records per (module, session_type, trimester)
# ---------------------------------------------------------------------------

def aggregate(records):
    """
    Returns dict keyed by (module_code, session_type, tri) with:
        'rooms': Counter of room codes
        'profs': set of professor name strings
    """
    agg = defaultdict(lambda: {'rooms': Counter(), 'profs': set()})
    for r in records:
        key = (r['module_code'], r['session_type'], r['tri'])
        if r['room']:
            agg[key]['rooms'][r['room']] += 1
        for p in r['profs']:
            norm = _normalise_name(p)
            if norm:
                agg[key]['profs'].add(norm)
    return agg


# ---------------------------------------------------------------------------
# Phase 3 — DB helpers
# ---------------------------------------------------------------------------

SKIP_PROF_RE = re.compile(r'temp\s*staff', re.IGNORECASE)


def get_or_create_professor(name_norm):
    """Find or create a Professor+User by normalised name. Returns Professor or None."""
    if not name_norm:
        return None
    if SKIP_PROF_RE.search(name_norm):
        return None   # SIT placeholder — not a real person

    # Try exact match
    user = User.query.filter(
        User.name == name_norm, User.role == 'professor'
    ).first()
    if user:
        return Professor.query.filter_by(user_id=user.id).first()

    # Try case-insensitive last-word match (surname)
    last_word = name_norm.split()[-1]
    user = User.query.filter(
        User.name.ilike(f'%{last_word}%'), User.role == 'professor'
    ).first()
    if user:
        return Professor.query.filter_by(user_id=user.id).first()

    # Create new
    staff_id = _make_staff_id(name_norm)
    base = staff_id
    i = 1
    while Professor.query.filter_by(staff_id=staff_id).first():
        staff_id = f'{base}{i}'
        i += 1

    email = f'{staff_id}@sit.edu.sg'
    base_email = email
    i = 1
    while User.query.filter_by(email=email).first():
        email = f'{staff_id}{i}@sit.edu.sg'
        i += 1

    user = User(name=name_norm, email=email, role='professor')
    user.set_password(secrets.token_urlsafe(24))
    db.session.add(user)
    db.session.flush()
    prof = Professor(user_id=user.id, staff_id=staff_id, department='Engineering')
    db.session.add(prof)
    db.session.flush()
    print(f'  [NEW PROF] {name_norm} → {email}')
    return prof


def get_or_create_room(room_code):
    """Find or create a Room by room_code. Returns Room or None."""
    if not room_code:
        return None
    room = Room.query.filter_by(room_code=room_code).first()
    if room:
        return room
    rtype = _room_type(room_code)
    cap   = _room_capacity(rtype)
    room  = Room(room_code=room_code, room_type=rtype, capacity=cap,
                 building=room_code.split('-')[0] if '-' in room_code else 'SIT')
    db.session.add(room)
    db.session.flush()
    print(f'  [NEW ROOM] {room_code} (type={rtype}, cap={cap})')
    return room


# ---------------------------------------------------------------------------
# Phase 4 — Apply updates to DB
# ---------------------------------------------------------------------------

def apply_updates(agg):
    prof_links_updated = 0
    rooms_updated = 0
    backbone_rooms_updated = 0

    for (module_code, session_type, tri_num), data in sorted(agg.items()):
        tri_key = f'{AY}-T{tri_num}'
        print(f'\n--- {module_code} {session_type} T{tri_num} ---')

        # Find ClassSession(s) for this module + session_type
        css = (ClassSession.query
               .join(ClassSession.course)
               .filter(Course.module_code == module_code,
                       ClassSession.session_type == session_type)
               .all())
        if not css:
            print(f'  [SKIP] No ClassSession found for {module_code} {session_type}')
            continue

        # --- Update professor links ---
        prof_objs = []
        for name in sorted(data['profs']):
            p = get_or_create_professor(name)
            if p:
                prof_objs.append(p)

        for cs in css:
            # Clear existing assumed professor links
            ClassSessionProfessor.query.filter_by(session_id=cs.id).delete()
            db.session.flush()
            # Add real professors
            for order, prof in enumerate(prof_objs):
                db.session.add(ClassSessionProfessor(
                    session_id=cs.id,
                    professor_id=prof.id,
                    is_primary=(order == 0),
                ))
            db.session.flush()
            prof_names = ', '.join(p.user.name for p in prof_objs) or '(none found)'
            print(f'  [PROFS] cs.id={cs.id} → {prof_names}')
            prof_links_updated += 1

        # --- Most common room from Excel ---
        if data['rooms']:
            best_room_code = data['rooms'].most_common(1)[0][0]
            room_obj = get_or_create_room(best_room_code)
            rooms_updated += 1

            # Update backbone TimetableEntries for this session in this trimester
            for cs in css:
                backbone_entries = TimetableEntry.query.filter_by(
                    trimester=tri_key,
                    class_session_id=cs.id,
                    is_backbone=True,
                ).all()
                for entry in backbone_entries:
                    if entry.room_id != (room_obj.id if room_obj else None):
                        old_room = entry.room.room_code if entry.room else 'None'
                        entry.room_id = room_obj.id if room_obj else None
                        print(f'  [ROOM] backbone entry id={entry.id}: {old_room} → {best_room_code}')
                        backbone_rooms_updated += 1
            db.session.flush()
        else:
            print(f'  [ROOM] No room data in Excel for this session — skip')

    db.session.commit()
    print(f'\nDone.')
    print(f'  Professor links updated : {prof_links_updated} ClassSessions')
    print(f'  Distinct rooms ensured  : {rooms_updated}')
    print(f'  Backbone rooms updated  : {backbone_rooms_updated} entries')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print('Parsing T1 (Sheet tab)...')
    t1 = parse_structured(T1_FILE, 'Sheet', 1)
    print(f'  {len(t1)} rows')

    print('Parsing T2 (raw tab)...')
    t2 = parse_structured(T2_FILE, 'raw', 2)
    print(f'  {len(t2)} rows')

    print('Parsing T3 (Year 1 grid)...')
    t3a = parse_grid(T3_FILE, 'Year 1', 3)
    print(f'  {len(t3a)} unique slots')
    print('Parsing T3 (Year 2 grid)...')
    t3b = parse_grid(T3_FILE, 'Year 2', 3)
    print(f'  {len(t3b)} unique slots')

    all_records = t1 + t2 + t3a + t3b
    agg = aggregate(all_records)

    print(f'\nAggregated {len(agg)} unique (module, session_type, trimester) combinations:')
    for (mod, stype, tri), d in sorted(agg.items()):
        best_room = d['rooms'].most_common(1)[0][0] if d['rooms'] else '—'
        print(f'  T{tri} {mod:12s} {stype:10s}  room={best_room}  profs={sorted(d["profs"])}')

    print('\nApplying updates...')
    app = create_app()
    with app.app_context():
        apply_updates(agg)


if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        # Re-parse inside app context so DB is ready
        print('Parsing T1 (Sheet tab)...')
        t1 = parse_structured(T1_FILE, 'Sheet', 1)
        print(f'  {len(t1)} rows')

        print('Parsing T2 (raw tab)...')
        t2 = parse_structured(T2_FILE, 'raw', 2)
        print(f'  {len(t2)} rows')

        print('Parsing T3 (Year 1 grid)...')
        t3a = parse_grid(T3_FILE, 'Year 1', 3)
        print(f'  {len(t3a)} unique slots')
        print('Parsing T3 (Year 2 grid)...')
        t3b = parse_grid(T3_FILE, 'Year 2', 3)
        print(f'  {len(t3b)} unique slots')

        all_records = t1 + t2 + t3a + t3b
        agg = aggregate(all_records)

        print(f'\n{len(agg)} (module, session_type, trimester) combinations found:')
        for (mod, stype, tri), d in sorted(agg.items()):
            best_room = d['rooms'].most_common(1)[0][0] if d['rooms'] else '—'
            print(f'  T{tri} {mod:12s} {stype:10s}  room={best_room}  profs={sorted(d["profs"])}')

        print('\nApplying updates to DB...')
        apply_updates(agg)
