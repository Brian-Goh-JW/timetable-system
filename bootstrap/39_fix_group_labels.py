"""
STEP 39 — Data fix: recompute ClassSession.group_label for every session,
using the real convention confirmed from Ms. Yang's Template 2 reference
file (Worksheet in ITP Project Requirements (Template 2).xlsx).

"Group" is a letter (matching Class Type) plus a sequential number
identifying which parallel split-section a session belongs to - e.g. a
Tutorial split into 4 parallel groups is T1/T2/T3/T4. "All" means unsplit
(the whole cohort attends together). Quiz is the one confirmed exception -
the reference file always numbers it (Q1), never "All", even for a single
quiz group.

Before this fix, group_label was set by a regex in template1_parser.py that
only matched the literal word "Group" inside a free-text Remarks cell -
almost never present, so 763 of 791 sessions silently defaulted to "All",
and the handful of real matches often grabbed garbage ("ASSESSMENTS",
"SETTING", "WORK", "PROJECTS" - Remarks text that happened to contain
"Group" followed by an unrelated word). This script replaces every value
with one derived from real sibling counts instead.

Lab -> P and Seminar -> S have no real example in the reference file
(only Lecture/Lectorial/Tutorial/Workshop/Quiz were directly observed) -
confirmed with Brian as a disclosed best-fit: P avoids colliding with
Lecture's L, S matches "Seminar" itself. See GROUP_LABEL_PREFIX in
app/routes/admin.py, which this script reuses directly.

Run ONCE:
    python bootstrap/39_fix_group_labels.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

from collections import defaultdict
from app import create_app, db
from app.models.class_session import ClassSession
from app.routes.admin import GROUP_LABEL_PREFIX

app = create_app()

with app.app_context():
    sessions = ClassSession.query.order_by(ClassSession.id).all()

    by_group = defaultdict(list)
    for s in sessions:
        by_group[(s.course_id, s.session_type)].append(s)

    changed = 0
    for (course_id, session_type), siblings in by_group.items():
        letter = GROUP_LABEL_PREFIX.get(session_type, 'X')
        if len(siblings) == 1 and session_type != 'quiz':
            new_labels = ['All']
        else:
            new_labels = [f'{letter}{i}' for i in range(1, len(siblings) + 1)]

        for s, new_label in zip(siblings, new_labels):
            if s.group_label != new_label:
                print(f'  {s.course.module_code} #{s.id} ({session_type}): '
                      f'{s.group_label!r} -> {new_label!r}')
                s.group_label = new_label
                changed += 1

    db.session.commit()
    print(f'\nDone. {changed} of {len(sessions)} sessions updated, '
          f'{len(sessions) - changed} already correct.')
