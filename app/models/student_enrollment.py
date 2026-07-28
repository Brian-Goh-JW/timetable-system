from datetime import datetime, timezone

from app import db


class StudentEnrollment(db.Model):
    """A student's real module demand for one academic term."""

    __tablename__ = 'student_enrollments'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id', ondelete='CASCADE'), nullable=False)
    academic_year = db.Column(db.String(10), nullable=False)
    trimester = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='enrolled')
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    student = db.relationship('User', backref='enrollments')
    course = db.relationship('Course', backref='student_enrollments')

    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'course_id', 'academic_year', 'trimester',
            name='uq_student_course_term',
        ),
        db.CheckConstraint('trimester IN (1, 2, 3)', name='ck_enrollment_trimester'),
    )


class StudentSectionAssignment(db.Model):
    """Explicit student-to-section assignment replacing hash-based selection."""

    __tablename__ = 'student_section_assignments'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    class_session_id = db.Column(
        db.Integer, db.ForeignKey('class_sessions.id', ondelete='CASCADE'), nullable=False
    )
    academic_year = db.Column(db.String(10), nullable=False)
    trimester = db.Column(db.Integer, nullable=False)

    student = db.relationship('User', backref='section_assignments')
    class_session = db.relationship('ClassSession', backref='student_assignments')

    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'class_session_id', 'academic_year', 'trimester',
            name='uq_student_section_term',
        ),
    )
