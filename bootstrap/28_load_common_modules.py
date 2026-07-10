"""
STEP 28 — Cross-programme shared module linking ("Common Modules").

Reads 'Common modules.xlsx' (Module | Year | Programmes | Remarks — module cell
may list several '/'-separated codes that are the SAME class under different
programmes' own numbering, e.g. "ESE1101/SBE1101/ASE1011"). For each row, finds
every existing Course (across the programmes named in that row) whose
module_code matches one of the listed codes at that year level, and — only when
2 or more DIFFERENT programmes actually have a matching Course in the DB right
now — creates a SharedModuleGroup and links their LECTURE/LECTORIAL
ClassSessions to it (tutorials/labs are left unlinked; the "everyone in one
room together" requirement is about the shared lecture, not smaller
per-programme tutorial groups — this is a judgment call, flagged here and on
the System Info page, not sourced from the file itself).

The "Programmes" column is free text (e.g. "All programmes (except DSC, EDE &
METS) + CEG (ICT)"), so matching is best-effort against the programme codes
that actually exist in this DB. Nothing is guessed silently — every resolved
match, every skipped row, and every unresolved token is printed for review.

Run AFTER all programme course data is loaded (bootstrap/23):
    python bootstrap/28_load_common_modules.py [path_to_common_modules.xlsx]
"""
import sys, os, re

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from app import create_app, db
from app.models.course import Course
from app.models.programme import Programme
from app.models.shared_module_group import SharedModuleGroup

DEFAULT_PATH = r'C:\Users\brain\AppData\Local\Temp\claude\C--Users-brain-OneDrive-Documents-SIT-ProjectTimetable\0e44374e-3794-4de2-873c-e66fbc6f7593\scratchpad\data_provided\Provided Data\Common modules.xlsx'
LINKABLE_SESSION_TYPES = ('lecture', 'lectorial')


def _tokenize(text, known_codes):
    """Split free text on ',', '&', ' and ' into tokens, strip parenthetical
    qualifiers (e.g. "CEG (ICT)" -> "CEG"), match against known_codes.
    Returns (resolved: set[str], unresolved: list[str])."""
    parts = re.split(r',|&| and ', text)
    resolved, unresolved = set(), []
    for p in parts:
        p = p.strip().lstrip('+').strip()
        if not p:
            continue
        base = re.sub(r'\(.*?\)', '', p).strip()
        if base.upper() in known_codes:
            resolved.add(base.upper())
        else:
            unresolved.append(p)
    return resolved, unresolved


def parse_programmes(text, known_codes):
    """Best-effort parse of a 'Programmes' cell. Handles the
    'All programmes (except A, B & C)' pattern specially."""
    text = text.strip()
    m = re.match(r'All programmes\s*\(except\s*(.+?)\)', text, re.IGNORECASE)
    if m:
        excluded, excl_unresolved = _tokenize(m.group(1), known_codes)
        return set(known_codes) - excluded, excl_unresolved
    return _tokenize(text, known_codes)


def main(path):
    app = create_app()
    with app.app_context():
        known_codes = {p.code for p in Programme.query.all()}
        df = pd.read_excel(path, sheet_name=0)
        df.columns = [str(c).strip() for c in df.columns]

        groups_created = 0
        sessions_linked = 0

        print(f'Loaded {len(df)} rows from {path}\n')

        for _, row in df.iterrows():
            module_cell = str(row.get('Module', '')).strip()
            year = row.get('Year')
            prog_cell = str(row.get('Programmes', '')).strip()
            if not module_cell or pd.isna(year):
                continue
            year = int(year)
            codes = [c.strip().upper() for c in module_cell.split('/') if c.strip()]

            resolved_progs, unresolved_tokens = parse_programmes(prog_cell, known_codes)

            print(f'--- Row: {module_cell} (Year {year}) — "{prog_cell}"')
            if unresolved_tokens:
                print(f'    [UNRESOLVED TOKENS — not matched to any known programme]: {unresolved_tokens}')
            print(f'    Resolved programmes: {sorted(resolved_progs)}')

            matched_courses = []  # (programme_code, Course)
            for prog_code in sorted(resolved_progs):
                prog = Programme.query.filter_by(code=prog_code).first()
                if not prog:
                    continue
                course = Course.query.filter(
                    Course.programme_id == prog.id,
                    Course.year_level == year,
                    Course.module_code.in_(codes),
                ).first()
                if course:
                    matched_courses.append((prog_code, course))

            found_progs = {pc for pc, _ in matched_courses}
            missing_progs = sorted(resolved_progs - found_progs)
            if missing_progs:
                print(f'    No matching Course found yet for: {missing_progs} (not loaded into DB, or different module code)')

            distinct_progs = {pc for pc, _ in matched_courses}
            if len(distinct_progs) < 2:
                print(f'    [SKIPPED] Only {len(distinct_progs)} programme(s) currently have this course in the DB — nothing to link yet.\n')
                continue

            group = SharedModuleGroup(label=module_cell, year_level=year,
                                       remarks=f'Auto-matched from Common modules.xlsx: {prog_cell}')
            db.session.add(group)
            db.session.flush()  # get group.id
            groups_created += 1

            # Link only ONE lecture/lectorial session per programme (the earliest
            # by id). If a programme has 2+ weekly lecture sessions for this
            # course, forcing ALL of them into one shared slot would conflict
            # with that same programme's OTHER session for its own group (it
            # can't be in two places at once) — so any extras are deliberately
            # left unlinked and scheduled independently, logged below.
            linked_this_row = 0
            for prog_code, course in matched_courses:
                candidates = sorted(
                    [cs for cs in course.class_sessions if cs.session_type in LINKABLE_SESSION_TYPES],
                    key=lambda cs: cs.id,
                )
                if not candidates:
                    continue
                candidates[0].shared_module_group_id = group.id
                linked_this_row += 1
                if len(candidates) > 1:
                    extra_ids = [cs.id for cs in candidates[1:]]
                    print(f'    [NOTE] {prog_code} has {len(candidates)} lecture/lectorial sessions for this '
                          f'course — only session {candidates[0].id} linked; {extra_ids} left independent '
                          f'to avoid a same-group slot clash.')
            sessions_linked += linked_this_row
            print(f'    [LINKED] SharedModuleGroup #{group.id} — {len(matched_courses)} programmes '
                  f'({sorted(distinct_progs)}), {linked_this_row} session(s) linked (1 per programme)\n')

        db.session.commit()
        print(f'\nDone. {groups_created} SharedModuleGroup(s) created, {sessions_linked} ClassSession(s) linked.')


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    main(path)
