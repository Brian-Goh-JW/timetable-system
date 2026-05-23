from app import db


class StudentGroup(db.Model):
    __tablename__ = 'student_groups'

    id = db.Column(db.Integer, primary_key=True)
    programme_id = db.Column(db.Integer, db.ForeignKey('programmes.id'), nullable=False)
    year_level = db.Column(db.Integer, nullable=False)                          # 1, 2, or 3
    group_label = db.Column(db.String(20), nullable=False)                      # e.g. 'DSC-Y2' or 'DSC-Y2-A'
    intake_size = db.Column(db.Integer, nullable=False)                         # number of students in this group
    parent_id = db.Column(db.Integer, db.ForeignKey('student_groups.id'), nullable=True)  # NULL if top-level group

    programme = db.relationship('Programme', backref='student_groups')
    sub_groups = db.relationship('StudentGroup', backref=db.backref('parent', remote_side=[id]))

    def __repr__(self):
        return f'<StudentGroup {self.group_label} ({self.intake_size} students)>'
