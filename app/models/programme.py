from app import db


class Programme(db.Model):
    __tablename__ = 'programmes'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)        # e.g. 'DSC'
    name = db.Column(db.String(100), nullable=False)                    # e.g. 'Digital Supply Chain'
    cluster = db.Column(db.String(100), nullable=False)                 # e.g. 'Engineering'

    def __repr__(self):
        return f'<Programme {self.code} - {self.name}>'
