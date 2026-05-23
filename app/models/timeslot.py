from app import db


class TimeSlot(db.Model):
    __tablename__ = 'timeslots'

    id = db.Column(db.Integer, primary_key=True)
    day_of_week = db.Column(
        db.Enum('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'),
        nullable=False
    )
    period_label = db.Column(db.String(20), nullable=False)     # e.g. 'P1', 'P2', 'Lab AM', 'Lab PM', 'Lab EV'
    start_time = db.Column(db.Time, nullable=False)             # e.g. 08:15
    end_time = db.Column(db.Time, nullable=False)               # e.g. 10:15

    __table_args__ = (
        db.UniqueConstraint('day_of_week', 'period_label', name='uq_timeslot_day_period'),
    )

    def __repr__(self):
        return f'<TimeSlot {self.day_of_week} {self.period_label} ({self.start_time}-{self.end_time})>'
