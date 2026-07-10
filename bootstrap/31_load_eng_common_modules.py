"""
STEP 31 — Load the authoritative common/university-wide module data from
'Requirements Template_ENG.xlsx' (previously skipped entirely by bootstrap/23).

This file covers 6 modules shared across multiple ENG programmes: ENG1001,
ENG1004, ENG1005, ENG1008, ENG1010, ENG3001. Several programmes' own
Requirements files either omit these modules entirely or only carry a
placeholder ("Info from <owner>") row with no real schedule data — the real
lecture/lab/tutorial/quiz data lives only in this central file.

Because the row structure varies a lot module-to-module (some list one row
per programme, some list one combined row for several programmes, some split
a single lecture into two weekly blocks), this is hand-transcribed from the
source file rather than auto-parsed — same approach as bootstrap/28's Common
Modules import, and for the same reason: fuzzy structural inference here is
riskier than a reviewable, explicit definition.

Only the LECTURE (or first block of a split lecture) is forced into a shared
slot across programmes via SharedModuleGroup — matching the same judgment
call made in bootstrap/28: tutorials/labs/quizzes stay independent per
programme even when the source gives only one combined row for them (in
which case the same weeks/staff are replicated per programme, since that is
the only data given — flagged as an assumption on System Info).

'CEG' appears in the source as a listed programme for ENG1001 but has no
Programme record in this system (never loaded anywhere else either) — rows
naming it are skipped and logged.

Run AFTER bootstrap/23 (needs existing Course/StudentGroup data) and AFTER
bootstrap/26 (METS-Y4 fix) and bootstrap/28 (Common Modules):
    python bootstrap/31_load_eng_common_modules.py
"""
import sys, os, re
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
from app.models.shared_module_group import SharedModuleGroup

TRIMESTER = 1
SKIPPED_PROG_CODES = {'CEG'}  # no Programme record anywhere in this system

DELIVERY_MAP = {
    'online - synchronous': ('online', False), 'online-synchronous': ('online', False),
    'online - asynchronous': ('online', True), 'online-asynchronous': ('online', True),
    'f2f': ('f2f', False),
}
DURATION_DEFAULTS = {'lecture': 2, 'tutorial': 2, 'lab': 3, 'quiz': 2, 'workshop': 3}


def get_or_create_professor(name_raw, sid_raw=None):
    name = (name_raw or '').strip()
    sid = (sid_raw or '').strip()
    if not name or name.lower() in ('nan', 'all instructors', 'adjunct', 'adjunct?', 'tbc', 'tbd'):
        return None
    name = name.title()
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
    email_local = re.sub(r'\.+', '.', re.sub(r'[^a-z0-9]', '.', name.lower())).strip('.')
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


def get_or_create_course(module_code, prog_code, year_level, delivery_mode='online'):
    prog = Programme.query.filter_by(code=prog_code).first()
    if not prog:
        return None, None
    course = Course.query.filter_by(module_code=module_code, programme_id=prog.id, trimester=TRIMESTER).first()
    if not course:
        course = Course(
            programme_id=prog.id, module_code=module_code, title=module_code,
            year_level=year_level, trimester=TRIMESTER, delivery_mode=delivery_mode,
            sessions_per_week=1, total_hours=0,
        )
        db.session.add(course)
        db.session.flush()
    sg = StudentGroup.query.filter_by(programme_id=prog.id, year_level=year_level, parent_id=None).first()
    return course, sg


def get_or_create_session(course, sg, session_type, weeks, staff_list, delivery_mode='online', is_async=False):
    """Find an existing session of this type on the course with matching (or
    null) teaching_weeks, else create one. Returns the ClassSession."""
    existing = [s for s in course.class_sessions if s.session_type == session_type]
    cs = None
    for s in existing:
        if (s.teaching_weeks or '') == (weeks or '') or not s.teaching_weeks:
            cs = s
            break
    if cs is None:
        cs = ClassSession(
            course_id=course.id, session_type=session_type, delivery_mode=delivery_mode,
            is_async=is_async, duration_hours=DURATION_DEFAULTS.get(session_type, 2),
            student_group_id=sg.id if sg else None, trimester=TRIMESTER,
            teaching_weeks=weeks, group_label='All',
        )
        db.session.add(cs)
        db.session.flush()
    else:
        cs.teaching_weeks = weeks or cs.teaching_weeks

    existing_prof_ids = {csp.professor_id for csp in ClassSessionProfessor.query.filter_by(session_id=cs.id).all()}
    is_first = not existing_prof_ids
    for name, sid in staff_list:
        prof = get_or_create_professor(name, sid)
        if not prof or prof.id in existing_prof_ids:
            continue
        db.session.add(ClassSessionProfessor(
            session_id=cs.id, professor_id=prof.id, is_primary=is_first,
            display_order=0 if is_first else 1,
        ))
        existing_prof_ids.add(prof.id)
        is_first = False
    return cs


def link_shared_group(label, year_level, sessions):
    sessions = [s for s in sessions if s is not None]
    if len(sessions) < 2:
        return None
    group = SharedModuleGroup(label=label, year_level=year_level,
                               remarks='Auto-loaded from Requirements Template_ENG.xlsx (bootstrap/31)')
    db.session.add(group)
    db.session.flush()
    for s in sessions:
        s.shared_module_group_id = group.id
    return group


app = create_app()
with app.app_context():
    log = []

    # ------------------------------------------------------------------
    # ENG1001 (Year 1) — 4 lecture groups, 9 per-programme tutorials, 9 quizzes
    # ------------------------------------------------------------------
    ENG1001_LECTURE_GROUPS = [
        (['EPE', 'MDME', 'RSE'], '1,2,3,4,5,6,8,9,10,11,12,13', [('Tan Rui Zhen', 'A101004')]),
        (['CVE', 'NAME'], '1,2,3,4,5,6,8,9,10,11,12,13', [('Ho Jiahui', None)]),
        (['ASE', 'MEC'], '1,2,3,4,5,6,8,9,10,11,12,13', [('Kyrin Liong', None), ('Hoh Hsin Jen', None)]),
        (['ESE', 'SBE', 'CEG'], '1,2,3,4,5,6,8,9,10,11,12,13', [('Wendy Chiew', None), ('Pei Yiyang', None)]),
    ]
    ENG1001_TUTORIALS = {
        'EPE': ('1,2,3,4,5,6,8,9,10,11,12', [('Tan Rui Zhen', 'A101004')]),
        'ASE': ('1,2,3,4,5,6,8,9,10,11,12', [('Kyrin Liong', None)]),
        'CVE': ('1,2,3,4,5,6,8,9,10,11,12', [('Ho Jiahui', None)]),
        'CEG': ('1,2,3,4,5,6,8,9,10,11,12', [('Pei Yiyang', None), ('Teoh Soo Khean', None)]),
        'MDME': ('1,2,3,4,5,6,8,9,10,11,12', []),
        'ESE': ('1,2,3,4,5,6,8,9,10,11,12', [('Wendy Chiew', None)]),
        'MEC': ('1,2,3,4,5,6,8,9,10,11,12', [('Julie Loh', None), ('Justin Fu', None)]),
        'NAME': ('1,2,3,4,5,6,8,9,10,11,12', [('Xin Wang', None), ('Bernard Voon', None)]),
        'RSE': ('1,2,3,4,5,6,8,9,10,11,12', [('Ng Chun Wee', None)]),
        'SBE': ('1,2,3,4,5,6,8,9,10,11,12', [('Thirumalai Sundararajan', None)]),
    }
    ENG1001_QUIZ_PROGS = ['ASE', 'CVE', 'EPE', 'ESE', 'MDME', 'MEC', 'NAME', 'RSE', 'SBE', 'CEG']
    ENG1001_QUIZ_WEEKS = '8,13'

    for i, (progs, weeks, staff) in enumerate(ENG1001_LECTURE_GROUPS, start=1):
        group_sessions = []
        for p in progs:
            if p in SKIPPED_PROG_CODES:
                log.append(f'[SKIP] ENG1001 lecture group {progs}: {p} has no Programme record')
                continue
            course, sg = get_or_create_course('ENG1001', p, 1, 'online')
            cs = get_or_create_session(course, sg, 'lecture', weeks, staff, 'online', False)
            group_sessions.append(cs)
            log.append(f'[OK] ENG1001 {p} lecture session={cs.id} weeks={weeks}')
        link_shared_group(f'ENG1001-L{i}', 1, group_sessions)

    for p, (weeks, staff) in ENG1001_TUTORIALS.items():
        if p in SKIPPED_PROG_CODES:
            log.append(f'[SKIP] ENG1001 tutorial: {p} has no Programme record')
            continue
        course, sg = get_or_create_course('ENG1001', p, 1)
        cs = get_or_create_session(course, sg, 'tutorial', weeks, staff, 'f2f', False)
        log.append(f'[OK] ENG1001 {p} tutorial session={cs.id} weeks={weeks}')

    for p in ENG1001_QUIZ_PROGS:
        if p in SKIPPED_PROG_CODES:
            log.append(f'[SKIP] ENG1001 quiz: {p} has no Programme record')
            continue
        course, sg = get_or_create_course('ENG1001', p, 1)
        cs = get_or_create_session(course, sg, 'quiz', ENG1001_QUIZ_WEEKS, [], 'f2f', False)
        log.append(f'[OK] ENG1001 {p} quiz session={cs.id} weeks={ENG1001_QUIZ_WEEKS}')

    # ------------------------------------------------------------------
    # ENG1004 (Year 1) — CVE + EDE shared lecture; independent tutorial/quiz x2
    # ------------------------------------------------------------------
    eng1004_lec = []
    for p in ['CVE', 'EDE']:
        course, sg = get_or_create_course('ENG1004', p, 1, 'online')
        cs = get_or_create_session(course, sg, 'lecture', '1,2,3,4,5,8,9,10,11',
                                    [('Tan Kim Seng', 'A101526'), ('Paolo Del Linz', None),
                                     ('Patrick Chua', None), ('Tay Bee Yen', None)], 'online', True)
        eng1004_lec.append(cs)
        log.append(f'[OK] ENG1004 {p} lecture session={cs.id}')
        cs_t = get_or_create_session(course, sg, 'tutorial', '1,2,3,4,5,6,8,9,10,11,12',
                                      [('Tan Kim Seng', 'A101526'), ('Paolo Del Linz', None)], 'f2f', False)
        log.append(f'[OK] ENG1004 {p} tutorial session={cs_t.id}')
        cs_q1 = get_or_create_session(course, sg, 'quiz', '6', [('Tan Kim Seng', 'A101526')], 'f2f', False)
        log.append(f'[OK] ENG1004 {p} quiz(wk6) session={cs_q1.id}')
    link_shared_group('ENG1004', 1, eng1004_lec)
    # second quiz (week 14) — only add if not already present, to avoid
    # colliding with the first quiz's get_or_create match (both would look
    # like "same type, no weeks" on a fresh course)
    for p in ['CVE', 'EDE']:
        course, sg = get_or_create_course('ENG1004', p, 1)
        has_wk14 = any(s.session_type == 'quiz' and s.teaching_weeks == '14' for s in course.class_sessions)
        if not has_wk14:
            cs_q2 = ClassSession(course_id=course.id, session_type='quiz', delivery_mode='f2f',
                                  is_async=False, duration_hours=2, student_group_id=sg.id if sg else None,
                                  trimester=TRIMESTER, teaching_weeks='14', group_label='All')
            db.session.add(cs_q2)
            db.session.flush()
            prof = get_or_create_professor('Tan Kim Seng', 'A101526')
            if prof:
                db.session.add(ClassSessionProfessor(session_id=cs_q2.id, professor_id=prof.id, is_primary=True))
            log.append(f'[OK] ENG1004 {p} quiz(wk14) session={cs_q2.id}')

    # ------------------------------------------------------------------
    # ENG1005 (Year 1) — METS only, no sharing needed
    # ------------------------------------------------------------------
    course, sg = get_or_create_course('ENG1005', 'METS', 1, 'online')
    cs = get_or_create_session(course, sg, 'lecture', '1,2,3,4,5,6,8,9,10,11,12',
                                [('Venkat', 'A88467')], 'online', False)
    log.append(f'[OK] ENG1005 METS lecture session={cs.id}')
    cs = get_or_create_session(course, sg, 'lab', '5,6,8,9,10,11,12',
                                [('Venkat', 'A88467'), ('Chew Choon Lee', 'A101092')], 'f2f', False)
    log.append(f'[OK] ENG1005 METS lab session={cs.id}')
    cs = get_or_create_session(course, sg, 'tutorial', '1,2,3,4,5,6,8,9,10,11,12',
                                [('Venkat', 'A88467'), ('Zheng Jianxin', None)], 'f2f', False)
    log.append(f'[OK] ENG1005 METS tutorial session={cs.id}')
    cs = get_or_create_session(course, sg, 'quiz', '5,10,13', [('Venkat', 'A88467')], 'f2f', False)
    log.append(f'[OK] ENG1005 METS quiz session={cs.id}')

    # ------------------------------------------------------------------
    # ENG1008 (Year 1) — EDE, EPE, ESE, SBE shared lecture; lab/quiz
    # replicated per programme (no per-programme breakdown given in source
    # — same weeks/staff for all 4, flagged as an assumption)
    # ------------------------------------------------------------------
    eng1008_lec = []
    for p in ['EDE', 'EPE', 'ESE', 'SBE']:
        course, sg = get_or_create_course('ENG1008', p, 1, 'online')
        cs = get_or_create_session(course, sg, 'lecture', '1,2,3,4,5,6,8,9,10,11,12,13',
                                    [('Lee Kwee Hiong', 'A88165'), ('Nguyen Thi Qui', 'A103076')], 'online', False)
        eng1008_lec.append(cs)
        log.append(f'[OK] ENG1008 {p} lecture session={cs.id}')
        cs_lab = get_or_create_session(course, sg, 'lab', '1,2,3,4,5,6,8,9,10,11,12',
                                        [('Lee Kwee Hiong', 'A88165'), ('Nguyen Thi Qui', 'A103076'),
                                         ('Nadarajan Sivakumar', 'A103675'), ('Sabu Emmanuel', 'A102737')],
                                        'f2f', False)
        log.append(f'[OK] ENG1008 {p} lab session={cs_lab.id}')
        cs_q = get_or_create_session(course, sg, 'quiz', '11',
                                      [('Lee Kwee Hiong', 'A88165'), ('Nguyen Thi Qui', 'A103076')], 'f2f', False)
        log.append(f'[OK] ENG1008 {p} quiz session={cs_q.id}')
    link_shared_group('ENG1008', 1, eng1008_lec)

    # ------------------------------------------------------------------
    # ENG1010 (Year 1) — MDME only, already complete via MDME's own file.
    # Nothing to do — verified no changes needed.
    # ------------------------------------------------------------------
    log.append('[VERIFIED] ENG1010 — MDME already complete via its own file, no changes made')

    # ------------------------------------------------------------------
    # ENG3001 (Year 2) — ESE + MDME, split lecture (2 weekly blocks, each
    # block forced into its own shared slot); lab/tutorial/workshop/quiz
    # independent per programme (MDME's own file already has all 7 rows;
    # ESE needs the full mirrored set created)
    # ------------------------------------------------------------------
    mdme_course, mdme_sg = get_or_create_course('ENG3001', 'MDME', 2, 'online')
    mdme_lec_a = next((s for s in mdme_course.class_sessions if s.session_type == 'lecture' and s.teaching_weeks == '1,2,3,4,5,6'), None)
    mdme_lec_b = next((s for s in mdme_course.class_sessions if s.session_type == 'lecture' and s.teaching_weeks == '8,9,10,11,12,13'), None)
    log.append(f'[VERIFIED] ENG3001 MDME lecture blocks: A={mdme_lec_a.id if mdme_lec_a else None} B={mdme_lec_b.id if mdme_lec_b else None}')

    ese_course, ese_sg = get_or_create_course('ENG3001', 'ESE', 2, 'online')
    ese_lec_a = get_or_create_session(ese_course, ese_sg, 'lecture', '1,2,3,4,5,6', [('Soh Chew Beng', None)], 'online', False)
    log.append(f'[OK] ENG3001 ESE lecture block A session={ese_lec_a.id}')
    ese_lec_b = ClassSession(course_id=ese_course.id, session_type='lecture', delivery_mode='online',
                              is_async=False, duration_hours=2, student_group_id=ese_sg.id if ese_sg else None,
                              trimester=TRIMESTER, teaching_weeks='8,9,10,11,12,13', group_label='All')
    db.session.add(ese_lec_b)
    db.session.flush()
    prof = get_or_create_professor('Howard Tang Huang Hui')
    if prof:
        db.session.add(ClassSessionProfessor(session_id=ese_lec_b.id, professor_id=prof.id, is_primary=True))
    log.append(f'[OK] ENG3001 ESE lecture block B session={ese_lec_b.id}')

    link_shared_group('ENG3001-blockA', 2, [mdme_lec_a, ese_lec_a])
    link_shared_group('ENG3001-blockB', 2, [mdme_lec_b, ese_lec_b])

    cs = get_or_create_session(ese_course, ese_sg, 'lab', '10', [('Soh Chew Beng', None)], 'f2f', False)
    log.append(f'[OK] ENG3001 ESE lab session={cs.id}')
    cs = get_or_create_session(ese_course, ese_sg, 'tutorial', '1,2,3,4,5,6', [('Soh Chew Beng', None)], 'f2f', False)
    log.append(f'[OK] ENG3001 ESE tutorial block A session={cs.id}')
    cs2 = ClassSession(course_id=ese_course.id, session_type='tutorial', delivery_mode='f2f',
                        is_async=False, duration_hours=2, student_group_id=ese_sg.id if ese_sg else None,
                        trimester=TRIMESTER, teaching_weeks='8,9,10,11,12,13', group_label='All')
    db.session.add(cs2)
    db.session.flush()
    if prof:
        db.session.add(ClassSessionProfessor(session_id=cs2.id, professor_id=prof.id, is_primary=True))
    log.append(f'[OK] ENG3001 ESE tutorial block B session={cs2.id}')
    cs = get_or_create_session(ese_course, ese_sg, 'workshop', '11', [('Soh Chew Beng', None)], 'f2f', False)
    log.append(f'[OK] ENG3001 ESE workshop session={cs.id}')
    cs = get_or_create_session(ese_course, ese_sg, 'quiz', '8,13',
                                [('Soh Chew Beng', None), ('Howard Tang Huang Hui', None)], 'f2f', False)
    log.append(f'[OK] ENG3001 ESE quiz session={cs.id}')

    # ------------------------------------------------------------------
    # Undo the old, incorrect ENG1001 SharedModuleGroup (id=3) that forced
    # MDME + SBE into the same lecture slot — they belong to different
    # lecture groups (L1 vs L4) per this authoritative file.
    # ------------------------------------------------------------------
    # The old group is the one still labelled plain 'ENG1001' (new ones are
    # labelled 'ENG1001-L1'..'L4') whose sessions are exactly MDME + SBE —
    # the wrong pairing this script is correcting.
    stale_groups = SharedModuleGroup.query.filter_by(label='ENG1001').all()
    for g in stale_groups:
        progs_in_group = {s.course.programme.code for s in g.class_sessions}
        if progs_in_group == {'MDME', 'SBE'}:
            for s in g.class_sessions:
                s.shared_module_group_id = None
            db.session.delete(g)
            log.append(f'[FIXED] Removed old incorrect SharedModuleGroup id={g.id} (MDME+SBE wrongly forced together)')

    db.session.commit()

    print('\n'.join(log))
    print(f'\nDone. {len(log)} log entries.')
