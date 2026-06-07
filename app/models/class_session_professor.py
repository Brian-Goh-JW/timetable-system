from app import db


class ClassSessionProfessor(db.Model):
    __tablename__ = 'class_session_professors'

    id           = db.Column(db.Integer, primary_key=True)
    session_id   = db.Column(db.Integer, db.ForeignKey('class_sessions.id', ondelete='CASCADE'), nullable=False)
    professor_id = db.Column(db.Integer, db.ForeignKey('professors.id',    ondelete='CASCADE'), nullable=False)
    is_primary   = db.Column(db.Boolean, nullable=False, default=False)
    display_order= db.Column(db.Integer, nullable=False, default=0)   # 0 = primary, 1,2,... = co-teachers

    professor = db.relationship('Professor', backref='session_assignments')

    def __repr__(self):
        return f'<ClassSessionProfessor session={self.session_id} prof={self.professor_id} primary={self.is_primary}>'
