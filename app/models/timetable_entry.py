from app import db


class TimetableEntry(db.Model):
    __tablename__ = 'timetable_entries'

    id = db.Column(db.Integer, primary_key=True)
    class_session_id = db.Column(db.Integer, db.ForeignKey('class_sessions.id'), nullable=False)
    timeslot_id = db.Column(db.Integer, db.ForeignKey('timeslots.id'), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=True)                    # NULL for online sessions
    override_professor_id = db.Column(db.Integer, db.ForeignKey('professors.id'), nullable=True) # per-week professor override
    week_number = db.Column(db.Integer, nullable=False)                                          # 1-13, skipping Week 7
    trimester = db.Column(db.String(20), nullable=False)                                         # e.g. 'AY2526-T1'
    academic_year = db.Column(db.String(10), nullable=True)                                      # e.g. 'AY2526'
    is_published = db.Column(db.Boolean, nullable=False, default=False)                          # Admin publishes when ready
    is_manually_edited = db.Column(db.Boolean, nullable=False, default=False)                    # True if Admin edited post-generation
    is_backbone = db.Column(db.Boolean, nullable=False, default=False)                           # True for imported real timetable - never deleted by reset

    class_session      = db.relationship('ClassSession', backref='timetable_entries')
    timeslot           = db.relationship('TimeSlot', backref='timetable_entries')
    room               = db.relationship('Room', backref='timetable_entries')
    override_professor = db.relationship('Professor', foreign_keys=[override_professor_id])

    @property
    def effective_professor(self):
        """Returns the professor teaching this specific week (override takes priority)."""
        return self.override_professor or self.class_session.primary_professor

    def __repr__(self):
        return f'<TimetableEntry session={self.class_session_id} week={self.week_number} slot={self.timeslot_id}>'
