from app import db
from datetime import datetime


class Event(db.Model):
    __tablename__ = 'events'

    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(200), nullable=False)
    description  = db.Column(db.Text, nullable=True)
    event_date   = db.Column(db.Date, nullable=False)
    is_full_day  = db.Column(db.Boolean, nullable=False, default=True)
    # Comma-separated timeslot IDs if not full day (e.g. "1,3,5")
    timeslot_ids = db.Column(db.String(200), nullable=True)
    # Scope: who is affected
    scope        = db.Column(db.Enum('school_wide', 'programme', 'course'),
                             nullable=False, default='school_wide')
    programme_id = db.Column(db.Integer, db.ForeignKey('programmes.id'), nullable=True)
    course_id    = db.Column(db.Integer, db.ForeignKey('courses.id'),    nullable=True)
    # What happens to affected sessions
    outcome      = db.Column(db.Enum('cancel', 'reschedule'), nullable=False, default='cancel')
    trimester    = db.Column(db.Integer, nullable=True)       # 1, 2, or 3
    academic_year= db.Column(db.String(10), nullable=True)    # e.g. 'AY2526'
    is_recurring = db.Column(db.Boolean, nullable=False, default=False)  # e.g. annual event
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    programme = db.relationship('Programme', backref='events')
    course    = db.relationship('Course',    backref='events')

    @property
    def blocked_timeslot_ids(self):
        """Return list of blocked timeslot IDs (empty = full day block)."""
        if self.is_full_day or not self.timeslot_ids:
            return []
        return [int(x) for x in self.timeslot_ids.split(',') if x.strip()]

    def __repr__(self):
        return f'<Event {self.event_date} {self.name}>'
