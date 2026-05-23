from app import db
from datetime import datetime


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'

    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    trimester     = db.Column(db.String(20), nullable=False)
    action        = db.Column(db.String(50), nullable=False)   # 'edit_week' | 'edit_all_weeks'
    module_code   = db.Column(db.String(20), nullable=True)
    session_type  = db.Column(db.String(20), nullable=True)
    week_label    = db.Column(db.String(30), nullable=True)    # 'Week 3' or 'All weeks'

    # Detailed old → new values
    old_timeslot  = db.Column(db.String(100), nullable=True)
    new_timeslot  = db.Column(db.String(100), nullable=True)
    old_room      = db.Column(db.String(50),  nullable=True)
    new_room      = db.Column(db.String(50),  nullable=True)
    old_professor = db.Column(db.String(100), nullable=True)
    new_professor = db.Column(db.String(100), nullable=True)

    timestamp     = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship('User', backref='audit_logs')

    def __repr__(self):
        return f'<AuditLog {self.action} {self.module_code} {self.week_label} by user={self.user_id}>'
