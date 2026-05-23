from app import db
from datetime import datetime


class AvailabilityDeclaration(db.Model):
    __tablename__ = 'availability_declarations'

    id = db.Column(db.Integer, primary_key=True)
    professor_id = db.Column(db.Integer, db.ForeignKey('professors.id'), nullable=False)
    timeslot_id = db.Column(db.Integer, db.ForeignKey('timeslots.id'), nullable=False)
    constraint_type = db.Column(db.Enum('strict', 'preferred'), nullable=True)     # set by Admin; NULL until classified
    reason = db.Column(db.String(255), nullable=True)                               # Professor's stated reason
    status = db.Column(db.Enum('pending', 'classified'), nullable=False, default='pending')
    submitted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    professor = db.relationship('Professor', backref='availability_declarations')
    timeslot = db.relationship('TimeSlot', backref='availability_declarations')

    def __repr__(self):
        return f'<AvailabilityDeclaration prof={self.professor_id} slot={self.timeslot_id} [{self.status}]>'
