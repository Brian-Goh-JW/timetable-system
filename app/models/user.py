from app import db, login_manager
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.Enum('admin', 'professor', 'student'), nullable=False)

    # Student-only: which sub-group (e.g. DSC-Y1-A) this account belongs to.
    # NULL for admin and professor accounts.
    student_group_id = db.Column(
        db.Integer,
        db.ForeignKey('student_groups.id', ondelete='SET NULL'),
        nullable=True,
    )
    student_group = db.relationship('StudentGroup', foreign_keys=[student_group_id])

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.email} ({self.role})>'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
