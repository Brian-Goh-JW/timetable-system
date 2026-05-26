"""
STEP 1 — Create the admin login account.
Run once after db.create_all() (Step 6 in README).

Usage:
    python bootstrap/1_seed_admin.py

Creates in table: users
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app import create_app, db
from app.models.user import User

# -------------------------------------------------
# Change these values before running if needed
# -------------------------------------------------
ADMIN_NAME     = 'Admin'
ADMIN_EMAIL    = 'admin@sit.edu.sg'
ADMIN_PASSWORD = 'Admin1234!'
# -------------------------------------------------

if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        existing = User.query.filter_by(email=ADMIN_EMAIL).first()
        if existing:
            print(f'Admin account already exists: {ADMIN_EMAIL}')
        else:
            admin = User(name=ADMIN_NAME, email=ADMIN_EMAIL, role='admin')
            admin.set_password(ADMIN_PASSWORD)
            db.session.add(admin)
            db.session.commit()
            print(f'Admin account created.')
            print(f'  Email   : {ADMIN_EMAIL}')
            print(f'  Password: {ADMIN_PASSWORD}')
            print()
            print('Change the password after your first login.')
