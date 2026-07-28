from app import db


class RoomAvailability(db.Model):
    """A room closure/maintenance block for a weekly slot and optional term."""

    __tablename__ = 'room_unavailability'

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id', ondelete='CASCADE'), nullable=False)
    timeslot_id = db.Column(db.Integer, db.ForeignKey('timeslots.id', ondelete='CASCADE'), nullable=False)
    academic_year = db.Column(db.String(10), nullable=True)
    trimester = db.Column(db.Integer, nullable=True)
    reason = db.Column(db.String(255), nullable=True)

    room = db.relationship('Room', backref='unavailability_blocks')
    timeslot = db.relationship('TimeSlot')

    __table_args__ = (
        db.UniqueConstraint(
            'room_id', 'timeslot_id', 'academic_year', 'trimester',
            name='uq_room_slot_term',
        ),
    )
