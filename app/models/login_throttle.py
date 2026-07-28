from datetime import datetime, timezone

from app import db


class LoginThrottle(db.Model):
    """Persistent failed-login window shared by all application workers."""

    __tablename__ = 'login_throttles'

    key_hash = db.Column(db.String(64), primary_key=True)
    failure_count = db.Column(db.Integer, nullable=False, default=0)
    window_started_at = db.Column(db.DateTime(timezone=True), nullable=False)
    blocked_until = db.Column(db.DateTime(timezone=True), nullable=True)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
