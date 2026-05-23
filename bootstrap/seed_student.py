"""
Creates a test Student user account for demo purposes.
Run once after publishing the timetable.

Usage:
    python bootstrap/seed_student.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from app.models.user import User

STUDENT_NAME     = 'Test Student'
STUDENT_EMAIL    = 'student@sit.edu.sg'
STUDENT_PASSWORD = 'Student1234!'

if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        existing = User.query.filter_by(email=STUDENT_EMAIL).first()
        if existing:
            print(f'Student account already exists: {STUDENT_EMAIL}')
        else:
            student = User(name=STUDENT_NAME, email=STUDENT_EMAIL, role='student')
            student.set_password(STUDENT_PASSWORD)
            db.session.add(student)
            db.session.commit()
            print(f'Student account created.')
            print(f'  Email   : {STUDENT_EMAIL}')
            print(f'  Password: {STUDENT_PASSWORD}')
