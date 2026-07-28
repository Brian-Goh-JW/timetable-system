from app import db


class Professor(db.Model):
    __tablename__ = 'professors'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    staff_id = db.Column(db.String(20), unique=True, nullable=False)    # e.g. 'P001'
    department = db.Column(db.String(100), nullable=False)              # e.g. 'Supply Chain & Logistics'
    qualifications = db.Column(db.Text, nullable=True)                  # comma-separated module/subject tags
    max_weekly_hours = db.Column(db.Integer, nullable=True)
    max_daily_hours = db.Column(db.Integer, nullable=True)
    home_building = db.Column(db.String(20), nullable=True)

    user = db.relationship('User', backref=db.backref('professor_profile', uselist=False))

    @property
    def qualification_tags(self):
        return {tag.strip().lower() for tag in (self.qualifications or '').split(',') if tag.strip()}

    def __repr__(self):
        return f'<Professor {self.staff_id} - {self.user.name if self.user else "Unknown"}>'
