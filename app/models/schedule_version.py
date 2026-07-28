from datetime import datetime, timezone

from app import db


class ScheduleVersion(db.Model):
    """Immutable snapshot used for comparison, approval, and rollback."""

    __tablename__ = 'schedule_versions'

    id = db.Column(db.Integer, primary_key=True)
    trimester = db.Column(db.String(20), nullable=False, index=True)
    label = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='draft')
    source = db.Column(db.String(30), nullable=False, default='generation')
    entries_json = db.Column(db.Text, nullable=False)
    stats_json = db.Column(db.Text, nullable=False, default='{}')
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    creator = db.relationship('User')
