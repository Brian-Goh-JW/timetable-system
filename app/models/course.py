from app import db


class Course(db.Model):
    __tablename__ = 'courses'

    id = db.Column(db.Integer, primary_key=True)
    programme_id = db.Column(db.Integer, db.ForeignKey('programmes.id'), nullable=False)
    module_code = db.Column(db.String(20), nullable=False)                  # e.g. 'DSC3002A' (not unique - same module can run in multiple trimesters)
    trimester = db.Column(db.Integer, nullable=True)                        # 1, 2, or 3
    title = db.Column(db.String(150), nullable=False)                       # e.g. 'Logistics & Supply Chain'
    year_level = db.Column(db.Integer, nullable=False)                      # 1, 2, or 3
    delivery_mode = db.Column(db.Enum('f2f', 'online', 'hybrid'), nullable=False)
    sessions_per_week = db.Column(db.Integer, nullable=False, default=1)    # usually 1 or 2
    total_hours = db.Column(db.Integer, nullable=False)                     # total contact hours for trimester
    remarks = db.Column(db.Text, nullable=True)                             # raw remarks from Excel, Admin reference only
    split_count = db.Column(db.Integer, nullable=True)                      # Admin-set f2f sub-group count; NULL blocks scheduler
    official_year_range = db.Column(db.String(20), nullable=True)           # e.g. 'Year 2-4' - SIT catalog info only,
                                                                              # admin self-input, never read by the solver
                                                                              # (a module's scheduling year_level above can
                                                                              # differ from its official catalog span)

    programme = db.relationship('Programme', backref='courses')

    def __repr__(self):
        return f'<Course {self.module_code} - {self.title}>'
