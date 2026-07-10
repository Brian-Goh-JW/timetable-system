from app import db


class ClassSession(db.Model):
    __tablename__ = 'class_sessions'

    id                = db.Column(db.Integer, primary_key=True)
    course_id         = db.Column(db.Integer, db.ForeignKey('courses.id'),         nullable=False)
    session_type          = db.Column(db.Enum('lecture', 'lab', 'seminar', 'tutorial',
                                              'lectorial', 'workshop', 'quiz'),      nullable=False)
    delivery_mode         = db.Column(db.Enum('f2f', 'online'),                      nullable=False)
    is_async              = db.Column(db.Boolean, nullable=False, default=False)      # True = online async, no timeslot needed
    duration_hours        = db.Column(db.Integer,                                     nullable=False)
    student_group_id      = db.Column(db.Integer, db.ForeignKey('student_groups.id'), nullable=True)
    fixed_timeslot_id     = db.Column(db.Integer, db.ForeignKey('timeslots.id'),      nullable=True)
    preferred_timeslot_id = db.Column(db.Integer, db.ForeignKey('timeslots.id'),      nullable=True)
    fixed_room_id         = db.Column(db.Integer, db.ForeignKey('rooms.id'),          nullable=True)
    # ^ locks this session to one specific room (the solver still picks the time),
    # set when the source data names an exact venue - see bootstrap/34.
    trimester             = db.Column(db.Integer,                                      nullable=True)  # 1, 2, or 3
    teaching_weeks        = db.Column(db.String(100), nullable=True)                  # e.g. "1,2,3,4,5,6,8,9,10,11,12,13"
    group_label           = db.Column(db.String(20),  nullable=True)                  # Template 2 group: "All", "T1", "L1", "P1"
    shared_module_group_id = db.Column(db.Integer, db.ForeignKey('shared_module_groups.id'), nullable=True)
    # ^ set when this session must be scheduled together with other programmes'
    # sessions for "the same" module (see SharedModuleGroup) - same room+slot, combined capacity.

    course             = db.relationship('Course',    backref='class_sessions')
    student_group      = db.relationship('StudentGroup', backref='class_sessions')
    fixed_timeslot     = db.relationship('TimeSlot',  foreign_keys=[fixed_timeslot_id])
    preferred_timeslot = db.relationship('TimeSlot',  foreign_keys=[preferred_timeslot_id])
    fixed_room         = db.relationship('Room',      foreign_keys=[fixed_room_id])
    shared_module_group = db.relationship('SharedModuleGroup', backref='class_sessions')

    # All professor assignments - ordered by display_order (0 = primary)
    professor_assignments = db.relationship(
        'ClassSessionProfessor',
        backref='class_session',
        cascade='all, delete-orphan',
        order_by='ClassSessionProfessor.display_order',
    )

    # -----------------------------------------------------------------------
    # Convenience helpers
    # -----------------------------------------------------------------------

    @property
    def primary_professor(self):
        """Primary Professor object, or None."""
        for a in self.professor_assignments:
            if a.is_primary:
                return a.professor
        return self.professor_assignments[0].professor if self.professor_assignments else None

    @property
    def primary_professor_id(self):
        p = self.primary_professor
        return p.id if p else None

    @property
    def all_professors(self):
        """List of all Professor objects for this session."""
        return [a.professor for a in self.professor_assignments]

    @property
    def all_professor_ids(self):
        return [a.professor_id for a in self.professor_assignments]

    def __repr__(self):
        code = self.course.module_code if self.course else '?'
        return f'<ClassSession {code} {self.session_type} ({self.delivery_mode})>'
