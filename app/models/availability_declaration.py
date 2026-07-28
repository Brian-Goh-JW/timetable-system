from app import db
from datetime import datetime


class AvailabilityDeclaration(db.Model):
    __tablename__ = 'availability_declarations'

    id = db.Column(db.Integer, primary_key=True)
    professor_id = db.Column(db.Integer, db.ForeignKey('professors.id'), nullable=False)
    timeslot_id = db.Column(db.Integer, db.ForeignKey('timeslots.id'), nullable=False)
    # NULL means a reusable default.  A term-specific declaration carries one
    # or both values and is ignored outside that scope.
    academic_year = db.Column(db.String(10), nullable=True)
    trimester = db.Column(db.Integer, nullable=True)
    constraint_type = db.Column(db.Enum('strict', 'preferred'), nullable=True)     # set by Admin; NULL until classified
    reason = db.Column(db.String(255), nullable=True)                               # Professor's stated reason
    status = db.Column(db.Enum('pending', 'classified'), nullable=False, default='pending')
    submitted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.CheckConstraint(
            'trimester IS NULL OR trimester IN (1, 2, 3)',
            name='ck_availability_trimester',
        ),
        db.UniqueConstraint(
            'professor_id', 'timeslot_id', 'academic_year', 'trimester',
            name='uq_availability_prof_slot_term',
        ),
    )

    def applies_to(self, academic_year, trimester):
        return (
            (self.academic_year is None or self.academic_year == academic_year)
            and (self.trimester is None or self.trimester == trimester)
        )

    professor = db.relationship('Professor', backref='availability_declarations')
    timeslot = db.relationship('TimeSlot', backref='availability_declarations')

    def __repr__(self):
        return f'<AvailabilityDeclaration prof={self.professor_id} slot={self.timeslot_id} [{self.status}]>'
