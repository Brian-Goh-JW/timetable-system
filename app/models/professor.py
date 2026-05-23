from app import db


class Professor(db.Model):
    __tablename__ = 'professors'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    staff_id = db.Column(db.String(20), unique=True, nullable=False)    # e.g. 'P001'
    department = db.Column(db.String(100), nullable=False)              # e.g. 'Supply Chain & Logistics'

    user = db.relationship('User', backref=db.backref('professor_profile', uselist=False))

    def __repr__(self):
        return f'<Professor {self.staff_id} - {self.user.name if self.user else "Unknown"}>'
