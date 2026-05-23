from app import db


class ClassSession(db.Model):
    __tablename__ = 'class_sessions'

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id'), nullable=False)
    session_type = db.Column(db.Enum('lecture', 'lab', 'seminar', 'tutorial'), nullable=False)
    delivery_mode = db.Column(db.Enum('f2f', 'online'), nullable=False)     # hybrid courses split into separate sessions
    duration_hours = db.Column(db.Integer, nullable=False)                  # session length in hours e.g. 2, 3
    professor_id = db.Column(db.Integer, db.ForeignKey('professors.id'), nullable=True)         # assigned by Admin
    student_group_id = db.Column(db.Integer, db.ForeignKey('student_groups.id'), nullable=True) # assigned after split
    fixed_timeslot_id = db.Column(db.Integer, db.ForeignKey('timeslots.id'), nullable=True)     # optional hard pin

    course = db.relationship('Course', backref='class_sessions')
    professor = db.relationship('Professor', backref='class_sessions')
    student_group = db.relationship('StudentGroup', backref='class_sessions')
    fixed_timeslot = db.relationship('TimeSlot', foreign_keys=[fixed_timeslot_id])

    def __repr__(self):
        return f'<ClassSession {self.course.module_code if self.course else "?"} {self.session_type} ({self.delivery_mode})>'
