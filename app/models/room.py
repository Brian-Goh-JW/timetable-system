from app import db


class Room(db.Model):
    __tablename__ = 'rooms'

    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(db.String(20), unique=True, nullable=False)       # e.g. 'E2-01-01'
    building = db.Column(db.String(20), nullable=False)                     # e.g. 'E2', 'E6'
    capacity = db.Column(db.Integer, nullable=False)                        # max students the room fits
    room_type = db.Column(db.Enum('lecture', 'lab', 'seminar'), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)         # Admin can deactivate without deleting
    equipment = db.Column(db.Text, nullable=True)                           # comma-separated normalized tags
    is_accessible = db.Column(db.Boolean, nullable=False, default=False)

    @property
    def equipment_tags(self):
        return {tag.strip().lower() for tag in (self.equipment or '').split(',') if tag.strip()}

    def __repr__(self):
        return f'<Room {self.room_code} (cap: {self.capacity}, type: {self.room_type})>'
