"""
STEP 4 (Optional) — Create a test student login account for demo purposes.
Run once after publishing the timetable.

Usage:
    python bootstrap/4_seed_student.py

Creates in table: users
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from app.models.user import User
from app.models.student_group import StudentGroup

STUDENT_NAME = os.environ.get('SEED_STUDENT_NAME', 'Test Student')
STUDENT_EMAIL = os.environ.get('SEED_STUDENT_EMAIL', 'student@sit.edu.sg').strip().lower()
STUDENT_PASSWORD = os.environ.get('SEED_STUDENT_PASSWORD', '')

if __name__ == '__main__':
    if not STUDENT_PASSWORD:
        raise SystemExit(
            'Set SEED_STUDENT_PASSWORD to a strong temporary password before running this script.'
        )
    app = create_app()
    with app.app_context():
        demo_group = StudentGroup.query.filter_by(group_label='DSC-Y1').first()
        existing = User.query.filter_by(email=STUDENT_EMAIL).first()
        if existing:
            if existing.role != 'student':
                raise SystemExit(
                    f'{STUDENT_EMAIL} already belongs to a non-student account.'
                )
            existing.name = STUDENT_NAME
            existing.set_password(STUDENT_PASSWORD)
            if demo_group:
                existing.student_group = demo_group
            db.session.commit()
            print('Student account password updated.')
        else:
            student = User(
                name=STUDENT_NAME,
                email=STUDENT_EMAIL,
                role='student',
                student_group=demo_group,
            )
            student.set_password(STUDENT_PASSWORD)
            db.session.add(student)
            db.session.commit()
            print(f'Student account created.')
            print(f'  Email   : {STUDENT_EMAIL}')
            print('Use the password supplied through SEED_STUDENT_PASSWORD.')
