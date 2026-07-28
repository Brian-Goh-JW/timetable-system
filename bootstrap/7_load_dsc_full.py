"""
Full DSC data loader — wipes and reloads all DSC data from Excel.

Wipes:  timetable_entries, flag_responses, timetable_flags,
        availability_declarations, audit_logs, class_sessions,
        courses, student_groups, programmes,
        professor user accounts (keeps admin + optional configured accounts)

Keeps:  admin@sit.edu.sg, accounts listed in DSC_KEEP_USER_EMAILS, timeslots, rooms

Usage:
    python bootstrap/7_load_dsc_full.py
"""

import sys, os, datetime, secrets
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from app import create_app, db
from app.models.user import User
from app.models.professor import Professor
from app.models.programme import Programme
from app.models.course import Course
from app.models.class_session import ClassSession
from app.models.class_session_professor import ClassSessionProfessor
from app.models.student_group import StudentGroup
from app.models.timetable_entry import TimetableEntry
from app.models.timetable_flag import TimetableFlag
from app.models.flag_response import FlagResponse
from app.models.availability_declaration import AvailabilityDeclaration
from app.models.audit_log import AuditLog

DSC_EXCEL = os.environ.get('DSC_EXCEL_PATH', '').strip()
KEEP_EMAILS = {'admin@sit.edu.sg'} | {
    email.strip().lower()
    for email in os.environ.get('DSC_KEEP_USER_EMAILS', '').split(',')
    if email.strip()
}

TRIMESTER_SHEETS = [
    ('DSC Tri 1', 1),
    ('DSC Tri 2', 2),
    ('DSC Tri 3', 3),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session_type(activity):
    return {
        'lecture':    'lecture',
        'lectorial':  'lecture',
        'tutorial':   'tutorial',
        'laboratory': 'lab',
        'lab':        'lab',
        'workshop':   'seminar',
        'seminar':    'seminar',
        'event':      'seminar',
    }.get(str(activity).strip().lower(), 'lecture')


def _delivery_mode(raw, stype):
    if stype == 'tutorial':
        return 'f2f'
    raw = str(raw).strip().lower()
    return 'online' if 'online' in raw else 'f2f'


def _duration(stype):
    return 3 if stype == 'lab' else 2


def _parse_teaching_weeks(val):
    """Normalise teaching weeks to a comma-separated string."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return '1-13'
    # Excel converted '1-13' to a date → treat as all weeks
    if isinstance(val, (datetime.datetime, datetime.date)):
        return '1-13'
    val = str(val).strip()
    if not val or val.lower() in ('nan', 'none'):
        return '1-13'
    return val


def _normalise_name(raw):
    return str(raw).strip().title() if raw and str(raw).strip() not in ('', 'nan', 'None') else None


# ---------------------------------------------------------------------------
# Step 1 — Wipe
# ---------------------------------------------------------------------------

def wipe():
    print('Wiping existing data...')
    db.session.execute(db.text('SET FOREIGN_KEY_CHECKS=0'))

    for table in ['audit_logs', 'flag_responses', 'timetable_flags',
                  'availability_declarations', 'timetable_entries',
                  'class_sessions', 'courses', 'student_groups', 'programmes']:
        result = db.session.execute(db.text(f'DELETE FROM {table}'))
        print(f'  Deleted {result.rowcount} rows from {table}')

    # Delete professor profiles for non-kept users, then the user accounts
    keep_ids_query = db.session.execute(
        db.text('SELECT id FROM users WHERE email IN :emails'),
        {'emails': tuple(KEEP_EMAILS)}
    ).fetchall()
    keep_ids = [r[0] for r in keep_ids_query]

    if keep_ids:
        db.session.execute(db.text(
            'DELETE FROM professors WHERE user_id NOT IN :ids'
        ), {'ids': tuple(keep_ids)})
        result = db.session.execute(db.text(
            "DELETE FROM users WHERE role='professor' AND email NOT IN :emails"
        ), {'emails': tuple(KEEP_EMAILS)})
    else:
        db.session.execute(db.text('DELETE FROM professors'))
        result = db.session.execute(db.text("DELETE FROM users WHERE role='professor'"))

    print(f'  Deleted {result.rowcount} professor user accounts.')

    db.session.execute(db.text('SET FOREIGN_KEY_CHECKS=1'))
    db.session.commit()
    print('Wipe complete.\n')


# ---------------------------------------------------------------------------
# Step 2 — Load professors from all sheets (deduplicated by staff_id)
# ---------------------------------------------------------------------------

def load_professors(xl):
    print('Loading professors...')
    seen = {}  # staff_id -> (name, dept)

    for sheet, _ in TRIMESTER_SHEETS:
        df = pd.read_excel(xl, sheet_name=sheet, header=1)
        for col_name, col_id in [('Staff 1', 'Staff ID 1'), ('Staff 2', 'Staff ID 2')]:
            if col_name not in df.columns:
                continue
            for _, row in df.iterrows():
                name = _normalise_name(row.get(col_name))
                sid  = str(row.get(col_id, '')).strip() if pd.notna(row.get(col_id, '')) else None
                if not name or not sid or sid in ('nan', 'None', ''):
                    continue
                if sid not in seen:
                    seen[sid] = name

    created = 0
    for staff_id, name in seen.items():
        user = User(name=name, email=f'{staff_id.lower()}@sit.edu.sg', role='professor')
        user.set_password(secrets.token_urlsafe(24))
        db.session.add(user)
        db.session.flush()
        db.session.add(Professor(user_id=user.id, staff_id=staff_id, department='Engineering'))
        created += 1

    db.session.commit()
    print(f'  Created {created} professors.\n')


# ---------------------------------------------------------------------------
# Step 3 — Load student groups
# ---------------------------------------------------------------------------

def load_student_groups(xl, programme):
    print('Loading student groups...')
    seen = {}  # year_level -> class_size

    for sheet, _ in TRIMESTER_SHEETS:
        df = pd.read_excel(xl, sheet_name=sheet, header=1)
        df['Prog/Yr']    = df['Prog/Yr'].ffill()
        df['Class Size'] = df['Class Size'].ffill()

        for _, row in df.iterrows():
            prog_yr = str(row.get('Prog/Yr', '')).strip()
            if 'YR' not in prog_yr.upper():
                continue
            try:
                yr = int(prog_yr.upper().split('YR')[-1].strip().split('/')[0])
            except Exception:
                continue
            try:
                size = int(row.get('Class Size', 80))
            except Exception:
                size = 80
            if yr not in seen:
                seen[yr] = size

    created = 0
    for yr, size in sorted(seen.items()):
        label = f'DSC-Y{yr}'
        group = StudentGroup(
            programme_id=programme.id,
            year_level=yr,
            group_label=label,
            intake_size=size,
            parent_id=None,
        )
        db.session.add(group)
        created += 1

    db.session.commit()
    print(f'  Created {created} student groups.\n')


# ---------------------------------------------------------------------------
# Step 4 — Load courses and sessions
# ---------------------------------------------------------------------------

def load_courses_and_sessions(xl, programme):
    print('Loading courses and sessions...')
    prof_map = {p.staff_id: p for p in Professor.query.all()}
    courses_created = 0
    sessions_created = 0

    for sheet, trimester in TRIMESTER_SHEETS:
        df = pd.read_excel(xl, sheet_name=sheet, header=1)
        df['Prog/Yr']      = df['Prog/Yr'].ffill()
        df['Class Size']   = df['Class Size'].ffill()
        df['Module Code']  = df['Module Code'].ffill()
        df['Module Name']  = df['Module Name'].ffill() if 'Module Name' in df.columns else df.get('Module Code', df['Module Code'])

        df = df.dropna(subset=['Module Code'])
        df = df[df['Module Code'].astype(str).str.strip() != '']
        df = df[df['Activity'].notna()]

        for module_code, group in df.groupby('Module Code'):
            module_code = str(module_code).strip()
            title_col   = 'Module Name' if 'Module Name' in df.columns else 'Module Code'

            # Extract year level
            prog_yr = str(group['Prog/Yr'].iloc[0]).upper()
            try:
                yr = int(prog_yr.split('YR')[-1].strip().split('/')[0])
            except Exception:
                yr = 1

            title = str(group[title_col].iloc[0]).strip() if title_col in group.columns else module_code

            # Unique sessions: (Activity, Delivery Mode) pairs
            unique_sessions = (group[['Activity', 'Delivery Mode']]
                               .drop_duplicates()
                               .reset_index(drop=True))

            # Overall delivery mode for the course
            stypes = {_session_type(r['Activity']) for _, r in unique_sessions.iterrows()}
            dmodes = {_delivery_mode(r['Delivery Mode'], _session_type(r['Activity']))
                      for _, r in unique_sessions.iterrows()}
            if 'f2f' in dmodes and 'online' in dmodes:
                course_mode = 'hybrid'
            elif 'online' in dmodes:
                course_mode = 'online'
            else:
                course_mode = 'f2f'

            # Remarks — first non-null value
            remarks = None
            if 'Remarks' in group.columns:
                remarks = next(
                    (str(v).strip() for v in group['Remarks'] if pd.notna(v) and str(v).strip()),
                    None
                )

            course = Course(
                programme_id      = programme.id,
                module_code       = module_code,
                title             = title,
                year_level        = yr,
                delivery_mode     = course_mode,
                sessions_per_week = len(unique_sessions),
                total_hours       = len(unique_sessions) * 2 * 13,
                remarks           = remarks,
                split_count       = None,
                trimester         = trimester,
            )
            db.session.add(course)
            db.session.flush()

            for _, row in unique_sessions.iterrows():
                stype = _session_type(row['Activity'])
                dmode = _delivery_mode(row['Delivery Mode'], stype)

                # Match professors by staff_id from session rows
                session_rows = group[
                    (group['Activity'] == row['Activity']) &
                    (group['Delivery Mode'] == row['Delivery Mode'])
                ]

                prof1_id = None
                prof2_id = None
                for _, sr in session_rows.iterrows():
                    sid1 = str(sr.get('Staff ID 1', '')).strip() if pd.notna(sr.get('Staff ID 1', '')) else None
                    sid2 = str(sr.get('Staff ID 2', '')).strip() if pd.notna(sr.get('Staff ID 2', '')) else None
                    if sid1 and sid1 != 'nan' and sid1 in prof_map:
                        prof1_id = prof_map[sid1].id
                    if sid2 and sid2 != 'nan' and sid2 in prof_map:
                        prof2_id = prof_map[sid2].id
                    if prof1_id:
                        break

                cs = ClassSession(
                    course_id        = course.id,
                    session_type     = stype,
                    delivery_mode    = dmode,
                    duration_hours   = _duration(stype),
                    student_group_id = None,
                    trimester        = trimester,
                )
                db.session.add(cs)
                db.session.flush()

                order = 0
                if prof1_id:
                    db.session.add(ClassSessionProfessor(
                        session_id=cs.id, professor_id=prof1_id,
                        is_primary=True, display_order=0
                    ))
                    order = 1
                if prof2_id:
                    db.session.add(ClassSessionProfessor(
                        session_id=cs.id, professor_id=prof2_id,
                        is_primary=False, display_order=order
                    ))
                sessions_created += 1

            courses_created += 1

    db.session.commit()
    print(f'  Created {courses_created} courses and {sessions_created} class sessions.\n')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if not DSC_EXCEL or not os.path.isfile(DSC_EXCEL):
        raise SystemExit(
            'Set DSC_EXCEL_PATH to the source DSC workbook before running this loader.'
        )
    app = create_app()
    with app.app_context():

        print('=== DSC Full Data Loader ===\n')

        wipe()

        prog = Programme(code='DSC', name='Digital Supply Chain', cluster='Engineering')
        db.session.add(prog)
        db.session.flush()
        print(f'Programme DSC created.\n')

        xl = pd.ExcelFile(DSC_EXCEL)

        load_professors(xl)
        load_student_groups(xl, prog)
        load_courses_and_sessions(xl, prog)

        db.session.commit()
        print('=== Load complete. ===')
