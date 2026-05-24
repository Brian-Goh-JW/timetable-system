"""
One-time migration: updates the users.role ENUM to include 'professor'.
Run once if the role column was originally created without the 'professor' value.
Affects table: users — column: role

Run: python bootstrap/fix_role_enum.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db

app = create_app()
with app.app_context():
    sql = "ALTER TABLE users MODIFY COLUMN role ENUM('admin', 'professor', 'student') NOT NULL"
    db.session.execute(db.text(sql))
    db.session.commit()
    print("Done. users.role ENUM updated to include 'professor'.")
