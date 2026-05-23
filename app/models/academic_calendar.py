from app import db


class AcademicCalendar(db.Model):
    __tablename__ = 'academic_calendar'

    id = db.Column(db.Integer, primary_key=True)
    trimester = db.Column(db.String(20), nullable=False)            # e.g. '2025-T1'
    week_number = db.Column(db.Integer, nullable=False)             # 1 to 13
    start_date = db.Column(db.Date, nullable=False)                 # Monday of that week
    end_date = db.Column(db.Date, nullable=False)                   # Friday of that week
    is_term_break = db.Column(db.Boolean, nullable=False, default=False)        # True for Week 7
    is_public_holiday = db.Column(db.Boolean, nullable=False, default=False)    # True if PH falls in this week
    notes = db.Column(db.String(200), nullable=True)                # e.g. 'Deepavali - Thursday cancelled'

    __table_args__ = (
        db.UniqueConstraint('trimester', 'week_number', name='uq_calendar_trimester_week'),
    )

    def __repr__(self):
        return f'<AcademicCalendar {self.trimester} Week {self.week_number} ({self.start_date} to {self.end_date})>'
