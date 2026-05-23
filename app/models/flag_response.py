from app import db
from datetime import datetime


class FlagResponse(db.Model):
    __tablename__ = 'flag_responses'

    id = db.Column(db.Integer, primary_key=True)
    flag_id = db.Column(db.Integer, db.ForeignKey('timetable_flags.id'), nullable=False)
    professor_id = db.Column(db.Integer, db.ForeignKey('professors.id'), nullable=False)
    response = db.Column(db.Enum('can_proceed', 'cannot_proceed'), nullable=False)
    comments = db.Column(db.Text, nullable=True)                            # optional explanation from Professor
    responded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    flag = db.relationship('TimetableFlag', backref='responses')
    professor = db.relationship('Professor', backref='flag_responses')

    def __repr__(self):
        return f'<FlagResponse flag={self.flag_id} prof={self.professor_id} [{self.response}]>'
