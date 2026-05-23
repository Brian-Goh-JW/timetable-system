from app import db
from datetime import datetime


class TimetableFlag(db.Model):
    __tablename__ = 'timetable_flags'

    id = db.Column(db.Integer, primary_key=True)
    timetable_entry_id = db.Column(db.Integer, db.ForeignKey('timetable_entries.id'), nullable=False)
    professor_id = db.Column(db.Integer, db.ForeignKey('professors.id'), nullable=False)
    declaration_id = db.Column(db.Integer, db.ForeignKey('availability_declarations.id'), nullable=False)
    status            = db.Column(db.Enum('open', 'acknowledged', 'resolved'), nullable=False, default='open')
    response_deadline = db.Column(db.Date, nullable=True)                     # set by admin when sending notification
    notification_sent = db.Column(db.Boolean, nullable=False, default=False)  # True once email sent to professor
    created_at        = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    resolved_at       = db.Column(db.DateTime, nullable=True)                 # set when status → 'resolved'

    timetable_entry = db.relationship('TimetableEntry', backref='flags')
    professor = db.relationship('Professor', backref='timetable_flags')
    declaration = db.relationship('AvailabilityDeclaration', backref='timetable_flags')

    def __repr__(self):
        return f'<TimetableFlag entry={self.timetable_entry_id} prof={self.professor_id} [{self.status}]>'
