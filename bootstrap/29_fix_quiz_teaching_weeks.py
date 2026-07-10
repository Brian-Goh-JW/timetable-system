"""
STEP 29 — Data cleanup: strip out-of-range week numbers from quiz
ClassSession.teaching_weeks values.

Confirmed with Brian: week 13 is exam week, nothing after that is real
teaching. Values like 14, 15, 17, 30 found in quiz teaching_weeks are data
entry mistakes, not a convention.

Only touches records that have OTHER valid weeks (1-13) remaining after the
bad values are stripped. Records whose ONLY value is invalid are left
untouched and printed separately for manual follow-up — blindly emptying
them would destroy the only data point with no way to know what the
correct week should have been.

Run ONCE:
    python bootstrap/29_fix_quiz_teaching_weeks.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

from app import create_app, db
from app.models.class_session import ClassSession

MAX_VALID_WEEK = 13

app = create_app()

with app.app_context():
    quizzes = ClassSession.query.filter(
        ClassSession.session_type == 'quiz',
        ClassSession.teaching_weeks.isnot(None),
    ).all()

    fixed, skipped_empty = [], []

    for s in quizzes:
        weeks = [w.strip() for w in s.teaching_weeks.split(',') if w.strip()]
        try:
            nums = [int(w) for w in weeks]
        except ValueError:
            continue
        valid = [n for n in nums if 1 <= n <= MAX_VALID_WEEK]
        if len(valid) == len(nums):
            continue  # nothing invalid, skip

        if valid:
            old = s.teaching_weeks
            new = ','.join(str(n) for n in valid)
            s.teaching_weeks = new
            fixed.append((s.id, s.course.module_code, old, new))
        else:
            skipped_empty.append((s.id, s.course.module_code, s.teaching_weeks))

    print(f'Fixing {len(fixed)} record(s):')
    for sid, code, old, new in fixed:
        print(f'  session={sid:5} {code:12} "{old}" -> "{new}"')

    print(f'\nLeaving {len(skipped_empty)} record(s) UNTOUCHED (only invalid value, needs manual review):')
    for sid, code, old in skipped_empty:
        print(f'  session={sid:5} {code:12} "{old}"  <-- no valid week left if stripped, not modified')

    db.session.commit()
    print(f'\nDone. {len(fixed)} record(s) cleaned, {len(skipped_empty)} left for manual follow-up.')
