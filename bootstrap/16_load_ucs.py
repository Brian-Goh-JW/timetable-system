"""
Bootstrap 16 — Load UCS1001 for DSC students (T2 only).

Creates:
  - CPC programme
  - UCS1001 course (T2, Year 1, Seminar, 2hr)
  - 3 professor accounts (Lee Mei Peng, Rahimi, Allison Ching)
  - 3 student sub-groups under DSC-Y1 (S6, S7, S8)
  - 3 test student accounts (one per sub-group)
  - 6 ClassSessions (Tue + Thu per group)
  - Historical timetable entries from 2520 raw tab

Assumes DSC uses groups S6, S7, S8.
Run AFTER bootstrap/15 + bootstrap/12.

Usage:
    python bootstrap/16_load_ucs.py
"""

import sys, os, re, secrets
from datetime import datetime, timedelta, date, time as dt_time

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
from app.models.timeslot import TimeSlot
from app.models.room import Room
from app.models.academic_calendar import AcademicCalendar

RAW_FILE = os.environ.get('UCS_SOURCE_XLSX', '').strip()
if not RAW_FILE or not os.path.isfile(RAW_FILE):
    raise SystemExit('Set UCS_SOURCE_XLSX to the source timetable workbook.')
AY        = 'AY2526'
TRI_KEY   = 'AY2526-T2'
TRI_NUM   = 2

# DSC is assumed to use S6, S7, S8
GROUP_CONFIG = {
    'S6': {
        'professor_name': 'Lee Mei Peng Gael Regina',
        'email':          'lmeipeng@sit.edu.sg',
        'staff_id':       'LMEIPENG',
        'tue': (dt_time(12, 0), dt_time(14, 0)),
        'thu': (dt_time(14, 0), dt_time(16, 0)),
        'label':          'DSC-Y1-S6',
        'student_email':  'student_s6@sit.edu.sg',
        'student_name':   'Test Student S6',
    },
    'S7': {
        'professor_name': 'Muhammad Rahimi',
        'email':          'mrahimi@sit.edu.sg',
        'staff_id':       'MRAHIMI',
        'tue': (dt_time(16, 0), dt_time(18, 0)),
        'thu': (dt_time(16, 0), dt_time(18, 0)),
        'label':          'DSC-Y1-S7',
        'student_email':  'student_s7@sit.edu.sg',
        'student_name':   'Test Student S7',
    },
    'S8': {
        'professor_name': 'Allison Ching Cho Hui',
        'email':          'aching@sit.edu.sg',
        'staff_id':       'ACHING',
        'tue': (dt_time(16, 0), dt_time(18, 0)),
        'thu': (dt_time(16, 0), dt_time(18, 0)),
        'label':          'DSC-Y1-S8',
        'student_email':  'student_s8@sit.edu.sg',
        'student_name':   'Test Student S8',
    },
}

ROOM_MAP = {
    'E2-03-18-SR230': 'E2-03-18-SR230',
    'E2-06-08-SR254': 'E2-06-08-SR254',
    'W3-04-06-SR23':  'W3-04-06-SR23',
    'W3-04-08-SR24':  'W3-04-08-SR24',
}


def parse_dates(val):
    if not val or str(val).strip().upper() in ('', 'NAN'):
        return []
    results = []
    for part in str(val).split(','):
        part = part.strip()
        for fmt in ('%d-%b-%y', '%d-%b-%Y'):
            try:
                results.append(datetime.strptime(part, fmt).date())
                break
            except ValueError:
                pass
    return results


def find_timeslot(day, start_t, end_t):
    return TimeSlot.query.filter_by(
        day_of_week=day, start_time=start_t, end_time=end_t
    ).first()


def find_room(code):
    if not code or code.strip().lower() in ('online', 'nan', ''):
        return None
    code = code.strip()
    r = Room.query.filter(Room.room_code.ilike(code)).first()
    if r:
        return r
    # Try stripping -SR### suffix
    stripped = re.sub(r'-SR\d+$', '', code)
    r = Room.query.filter(Room.room_code.ilike(stripped)).first()
    return r


def week_for_date(d, cal_weeks):
    """Return the week_number from AcademicCalendar for the given date."""
    for cw in cal_weeks:
        if cw.start_date <= d <= cw.end_date:
            return cw.week_number
    return None


app = create_app()
with app.app_context():

    # ------------------------------------------------------------------
    # 1. CPC programme
    # ------------------------------------------------------------------
    cpc = Programme.query.filter_by(code='CPC').first()
    if not cpc:
        cpc = Programme(code='CPC', name='Common Programme Core', cluster='University-Wide')
        db.session.add(cpc)
        db.session.flush()
        print('Created CPC programme')
    else:
        print('CPC programme already exists')

    # ------------------------------------------------------------------
    # 2. UCS1001 course
    # ------------------------------------------------------------------
    ucs_course = Course.query.filter_by(module_code='UCS1001', trimester=TRI_NUM).first()
    if not ucs_course:
        ucs_course = Course(
            programme_id     = cpc.id,
            module_code      = 'UCS1001',
            title            = 'Ethics and Civic Engagement',
            year_level       = 1,
            delivery_mode    = 'f2f',
            sessions_per_week= 2,
            total_hours      = 52,
            trimester        = TRI_NUM,
            split_count      = 3,
        )
        db.session.add(ucs_course)
        db.session.flush()
        print('Created UCS1001 course')
    else:
        print('UCS1001 course already exists')

    # ------------------------------------------------------------------
    # 3. Professor accounts
    # ------------------------------------------------------------------
    prof_objs = {}
    for grp, cfg in GROUP_CONFIG.items():
        existing = User.query.filter_by(email=cfg['email']).first()
        if not existing:
            u = User(name=cfg['professor_name'], email=cfg['email'], role='professor')
            u.set_password(secrets.token_urlsafe(24))
            db.session.add(u)
            db.session.flush()
            p = Professor(user_id=u.id, staff_id=cfg['staff_id'], department='Common Programme Core')
            db.session.add(p)
            db.session.flush()
            prof_objs[grp] = p
            print(f'  Created professor: {cfg["professor_name"]}')
        else:
            p = Professor.query.filter_by(user_id=existing.id).first()
            prof_objs[grp] = p
            print(f'  Professor already exists: {cfg["professor_name"]}')

    db.session.commit()

    # ------------------------------------------------------------------
    # 4. DSC-Y1 parent group + sub-groups
    # ------------------------------------------------------------------
    dsc_y1 = StudentGroup.query.filter_by(group_label='DSC-Y1').first()
    if not dsc_y1:
        print('ERROR: DSC-Y1 student group not found — run bootstrap/7 first')
        sys.exit(1)

    sub_groups = {}
    for grp, cfg in GROUP_CONFIG.items():
        sg = StudentGroup.query.filter_by(group_label=cfg['label']).first()
        if not sg:
            sg = StudentGroup(
                programme_id = dsc_y1.programme_id,
                year_level   = 1,
                group_label  = cfg['label'],
                intake_size  = 50,
                parent_id    = dsc_y1.id,
            )
            db.session.add(sg)
            db.session.flush()
            print(f'  Created student sub-group: {cfg["label"]}')
        else:
            print(f'  Sub-group already exists: {cfg["label"]}')
        sub_groups[grp] = sg

    db.session.commit()

    # ------------------------------------------------------------------
    # 5. Test student accounts (one per UCS group)
    # ------------------------------------------------------------------
    for grp, cfg in GROUP_CONFIG.items():
        existing = User.query.filter_by(email=cfg['student_email']).first()
        if not existing:
            u = User(
                name             = cfg['student_name'],
                email            = cfg['student_email'],
                role             = 'student',
                student_group_id = sub_groups[grp].id,
            )
            u.set_password(secrets.token_urlsafe(24))
            db.session.add(u)
            print(f'  Created student: {cfg["student_email"]} → {cfg["label"]}')
        else:
            print(f'  Student already exists: {cfg["student_email"]}')

    db.session.commit()

    # ------------------------------------------------------------------
    # 6. ClassSessions — Tue + Thu per group (6 total)
    # ------------------------------------------------------------------
    session_map = {}  # (grp, day) -> ClassSession

    for grp, cfg in GROUP_CONFIG.items():
        for day_key, day_name in [('tue', 'Tuesday'), ('thu', 'Thursday')]:
            start_t, end_t = cfg[day_key]
            ts = find_timeslot(day_name, start_t, end_t)
            if not ts:
                print(f'  WARNING: No timeslot for {day_name} {start_t}-{end_t} — skipping {grp} {day_key}')
                continue

            existing_cs = ClassSession.query.filter_by(
                course_id        = ucs_course.id,
                session_type     = 'seminar',
                student_group_id = sub_groups[grp].id,
                trimester        = TRI_NUM,
                # Match by timeslot via fixed_timeslot_id is not used —
                # check delivery mode to distinguish Tue/Thu sessions indirectly
                delivery_mode    = 'f2f',
            ).filter(
                ClassSession.id.in_(
                    db.session.query(ClassSession.id).filter(
                        ClassSession.course_id        == ucs_course.id,
                        ClassSession.student_group_id == sub_groups[grp].id,
                    )
                )
            ).all()

            # Use a name check: re-query simply by counting
            cs_count = ClassSession.query.filter_by(
                course_id        = ucs_course.id,
                student_group_id = sub_groups[grp].id,
                trimester        = TRI_NUM,
            ).count()

            # Create if fewer than 2 sessions already for this group
            if cs_count < 2:
                cs = ClassSession(
                    course_id        = ucs_course.id,
                    session_type     = 'seminar',
                    delivery_mode    = 'f2f',
                    duration_hours   = 2,
                    student_group_id = sub_groups[grp].id,
                    trimester        = TRI_NUM,
                )
                db.session.add(cs)
                db.session.flush()

                prof = prof_objs.get(grp)
                if prof:
                    db.session.add(ClassSessionProfessor(
                        session_id    = cs.id,
                        professor_id  = prof.id,
                        is_primary    = True,
                        display_order = 0,
                    ))

                session_map[(grp, day_key)] = cs
                print(f'  Created ClassSession: UCS1001 {grp} {day_name} {start_t.strftime("%H:%M")}-{end_t.strftime("%H:%M")}')
            else:
                # Already exists — find by order (first = Tue, second = Thu)
                all_cs = ClassSession.query.filter_by(
                    course_id        = ucs_course.id,
                    student_group_id = sub_groups[grp].id,
                    trimester        = TRI_NUM,
                ).order_by(ClassSession.id).all()
                idx = 0 if day_key == 'tue' else 1
                if idx < len(all_cs):
                    session_map[(grp, day_key)] = all_cs[idx]
                print(f'  ClassSession already exists: UCS1001 {grp} {day_name}')

    db.session.commit()

    # ------------------------------------------------------------------
    # 7. Import timetable entries from 2520 raw tab
    # ------------------------------------------------------------------
    cal_weeks = AcademicCalendar.query.filter_by(trimester=TRI_KEY).order_by(
        AcademicCalendar.week_number
    ).all()

    if not cal_weeks:
        print('ERROR: No AcademicCalendar weeks for AY2526-T2 — run bootstrap/12 first')
        sys.exit(1)

    df = pd.read_excel(RAW_FILE, sheet_name='raw', header=0)
    ucs_df = df[df['Name'].str.contains('UCS1001', na=False)].copy()

    entries_created = 0
    used = set()

    for _, row in ucs_df.iterrows():
        name     = str(row['Name'])
        grp_m    = re.search(r'/S(\d+)$', name)
        sem_m    = re.search(r'SEM(\d*)', name)
        if not grp_m:
            continue

        grp_code = 'S' + grp_m.group(1)
        if grp_code not in GROUP_CONFIG:
            continue

        sem_variant = sem_m.group(0) if sem_m else 'SEM'
        # SEM/SEM2/SEM3 = Tuesday, SEM4/SEM5/SEM6 = Thursday
        sem_num  = int(sem_m.group(1)) if sem_m and sem_m.group(1) else 1
        day_key  = 'thu' if sem_num >= 4 else 'tue'

        cs = session_map.get((grp_code, day_key))
        if not cs:
            continue

        room_raw = str(row.get('Allocated Location Name', '')).strip()
        room     = find_room(room_raw)

        cfg      = GROUP_CONFIG[grp_code]
        start_t, end_t = cfg[day_key]
        day_name = 'Thursday' if day_key == 'thu' else 'Tuesday'
        ts       = find_timeslot(day_name, start_t, end_t)
        if not ts:
            continue

        dates = parse_dates(row.get('Activity Dates (Individual)', ''))
        for d in dates:
            wk = week_for_date(d, cal_weeks)
            if not wk:
                continue
            pair = (cs.id, wk)
            if pair in used:
                continue
            used.add(pair)
            db.session.add(TimetableEntry(
                class_session_id   = cs.id,
                timeslot_id        = ts.id,
                room_id            = room.id if room else None,
                week_number        = wk,
                trimester          = TRI_KEY,
                academic_year      = AY,
                is_published       = True,
                is_manually_edited = False,
                is_backbone        = True,
            ))
            entries_created += 1

    db.session.commit()
    print(f'\nUCS1001 timetable entries created: {entries_created}')
    print('\n=== Done. Next: publish AY2526-T2 entries in Admin > Timetable ===')
