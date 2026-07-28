"""
STEP 2 — Load course, room, and timeslot data from Excel/CSV source files.
Run once after 1_seed_admin.py.

Usage:
    python bootstrap/2_excel_loader.py

Update DSC_EXCEL and VENUE_CSV below to point to your local files before running.

Creates/populates tables:
    programmes, courses, class_sessions, rooms, timeslots
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from datetime import time
import pandas as pd
from app import create_app, db
from app.models.programme import Programme
from app.models.course import Course
from app.models.class_session import ClassSession
from app.models.room import Room
from app.models.timeslot import TimeSlot

# -------------------------------------------------
# Supply these paths through environment variables
# -------------------------------------------------
DSC_EXCEL = os.environ.get('DSC_SOURCE_XLSX', '').strip()
VENUE_CSV = os.environ.get('VENUE_CSV_PATH', '').strip()
if not os.path.isfile(DSC_EXCEL) or not os.path.isfile(VENUE_CSV):
    raise SystemExit('Set DSC_SOURCE_XLSX and VENUE_CSV_PATH before running this loader.')
# -------------------------------------------------


# ---------------------------------------------------------------------------
# Step 1 — Seed ENG period block timeslots
# ---------------------------------------------------------------------------

def seed_timeslots():
    """Insert all 35 ENG period-block timeslots (5 days x 7 periods)."""
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']

    # (label, start, end)  — times confirmed from Standard Period Block sheet
    periods = [
        ('P1',     time(9,  0), time(11, 0)),   # Others: 09:00-11:00
        ('P2',     time(12, 0), time(14, 0)),   # Others: 12:00-14:00
        ('P3',     time(14, 0), time(16, 0)),   # Others: 14:00-16:00
        ('P4',     time(16, 0), time(18, 0)),   # Others: 16:00-18:00
        ('Lab AM', time(9,  0), time(12, 0)),   # Labs:   09:00-12:00
        ('Lab PM', time(13, 0), time(16, 0)),   # Labs:   13:00-16:00
        ('Lab EV', time(16, 0), time(19, 0)),   # Labs:   16:00-19:00
    ]

    count = 0
    for day in days:
        for label, start, end in periods:
            exists = TimeSlot.query.filter_by(day_of_week=day, period_label=label).first()
            if not exists:
                db.session.add(TimeSlot(day_of_week=day, period_label=label,
                                        start_time=start, end_time=end))
                count += 1
    db.session.commit()
    print(f'  Seeded {count} timeslots.')


# ---------------------------------------------------------------------------
# Step 2 — Load DSC programme
# ---------------------------------------------------------------------------

def load_programme():
    """Create the DSC programme record if it does not already exist."""
    existing = Programme.query.filter_by(code='DSC').first()
    if existing:
        print('  Programme DSC already exists — skipping.')
        return existing
    prog = Programme(code='DSC', name='Digital Supply Chain', cluster='Engineering')
    db.session.add(prog)
    db.session.commit()
    print(f'  Created programme: {prog}')
    return prog


# ---------------------------------------------------------------------------
# Helpers for Step 3
# ---------------------------------------------------------------------------

def _delivery_mode(raw):
    raw = str(raw).strip().lower()
    return 'online' if 'online' in raw else 'f2f'


def _session_type(activity):
    return {
        'lecture':    'lecture',
        'tutorial':   'tutorial',
        'laboratory': 'lab',
        'lab':        'lab',
        'workshop':   'seminar',
        'event':      'seminar',    # events mapped to seminar (closest type)
        'seminar':    'seminar',
    }.get(str(activity).strip().lower(), 'lecture')


def _duration(session_type):
    """Labs are 3 hours; all other session types are 2 hours."""
    return 3 if session_type == 'lab' else 2


def _week_count(teaching_weeks_str):
    if pd.isna(teaching_weeks_str):
        return 0
    return len(str(teaching_weeks_str).split(','))


def _extract_year(prog_yr):
    try:
        return int(str(prog_yr).strip()[-1])
    except Exception:
        return 1


# ---------------------------------------------------------------------------
# Step 3 — Load courses and class sessions from DSC Excel
# ---------------------------------------------------------------------------

def load_courses_and_sessions(programme):
    df = pd.read_excel(DSC_EXCEL, sheet_name='Module', header=1)

    # Forward-fill merged cells so every row has its module code and year
    df['Prog/Yr']     = df['Prog/Yr'].ffill()
    df['Class Size']  = df['Class Size'].ffill()
    df['Module Code'] = df['Module Code'].ffill()

    # Drop entirely empty rows
    df = df.dropna(subset=['Module Code'])
    df = df[df['Module Code'].astype(str).str.strip() != '']
    df = df[df['Activity'].notna()]

    courses_created  = 0
    sessions_created = 0

    for module_code, group in df.groupby('Module Code'):
        module_code = str(module_code).strip()

        if Course.query.filter_by(module_code=module_code).first():
            print(f'  Course {module_code} already exists — skipping.')
            continue

        year_level = _extract_year(group['Prog/Yr'].iloc[0])

        # Unique sessions: one row per distinct (Activity, Delivery Mode) pair
        unique_sessions = (group[['Activity', 'Delivery Mode']]
                           .drop_duplicates()
                           .reset_index(drop=True))

        # Overall delivery mode for the course
        modes = {_delivery_mode(r['Delivery Mode']) for _, r in unique_sessions.iterrows()}
        if 'f2f' in modes and 'online' in modes:
            course_mode = 'hybrid'
        elif 'online' in modes:
            course_mode = 'online'
        else:
            course_mode = 'f2f'

        # Total contact hours: sum (duration x weeks) for the first occurrence of each unique session
        total_hours = 0
        seen = set()
        for _, row in group.iterrows():
            key = (str(row['Activity']).strip(), str(row['Delivery Mode']).strip())
            if key not in seen:
                stype = _session_type(row['Activity'])
                total_hours += _duration(stype) * _week_count(row['Teaching Weeks'])
                seen.add(key)
        if total_hours == 0:
            total_hours = len(unique_sessions) * 2 * 12   # safe fallback

        # Remarks — first non-null value across the group
        remarks = next(
            (str(v).strip() for v in group['Remarks'] if pd.notna(v) and str(v).strip()),
            None
        )

        course = Course(
            programme_id      = programme.id,
            module_code       = module_code,
            title             = module_code,          # no title column in Excel; Admin can update via web app
            year_level        = year_level,
            delivery_mode     = course_mode,
            sessions_per_week = len(unique_sessions),
            total_hours       = total_hours,
            remarks           = remarks,
            split_count       = None,                 # Admin must set this through the web app before scheduling
        )
        db.session.add(course)
        db.session.flush()      # get course.id before committing

        for _, row in unique_sessions.iterrows():
            stype = _session_type(row['Activity'])
            dmode = 'f2f' if stype == 'tutorial' else _delivery_mode(row['Delivery Mode'])
            db.session.add(ClassSession(
                course_id        = course.id,
                session_type     = stype,
                delivery_mode    = dmode,
                duration_hours   = _duration(stype),
                professor_id     = None,            # assigned by Admin through web app
                student_group_id = None,            # assigned after split count is set
            ))
            sessions_created += 1

        courses_created += 1

    db.session.commit()
    print(f'  Created {courses_created} courses and {sessions_created} class sessions.')


# ---------------------------------------------------------------------------
# Step 4 — Load rooms from Venue CSV
# ---------------------------------------------------------------------------

def _room_type(resource_type):
    rt = str(resource_type).strip().lower()
    if 'lectorial' in rt or 'auditorium' in rt:
        return 'lecture'
    elif 'lab' in rt or 'computer' in rt:
        return 'lab'
    else:
        return 'seminar'    # seminar rooms and anything else


def load_rooms():
    df = pd.read_csv(VENUE_CSV, encoding='latin-1')

    count = 0
    for _, row in df.iterrows():
        location = str(row['Location Name']).strip()

        # Room code is the first three dash-separated segments e.g. 'E2-01-01-Lectorial 6' -> 'E2-01-01'
        parts = location.split('-')
        if len(parts) >= 3:
            room_code = '-'.join(parts[:3])
            building  = parts[0]
        else:
            room_code = location
            building  = location[:2] if len(location) >= 2 else 'Unknown'

        if Room.query.filter_by(room_code=room_code).first():
            continue    # already loaded

        try:
            capacity = int(row['Capacity'])
        except (ValueError, TypeError):
            capacity = 30

        db.session.add(Room(
            room_code  = room_code,
            building   = building,
            capacity   = capacity,
            room_type  = _room_type(row['Resource Type']),
            is_active  = True,
        ))
        count += 1

    db.session.commit()
    print(f'  Loaded {count} rooms.')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        print('Step 1: Seeding ENG timeslots...')
        seed_timeslots()

        print('Step 2: Loading DSC programme...')
        programme = load_programme()

        print('Step 3: Loading courses and class sessions...')
        load_courses_and_sessions(programme)

        print('Step 4: Loading rooms...')
        load_rooms()

        print('\nBootstrap complete. All source files are no longer needed.')
