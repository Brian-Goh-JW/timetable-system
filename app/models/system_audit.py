from datetime import datetime, timezone

from app import db


class SystemAudit(db.Model):
    """Append-only record of meaningful administrator changes."""

    __tablename__ = 'system_audits'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    action = db.Column(db.String(120), nullable=False, index=True)
    entity_type = db.Column(db.String(80), nullable=True)
    entity_id = db.Column(db.String(80), nullable=True)
    summary = db.Column(db.String(500), nullable=True)
    metadata_json = db.Column(db.Text, nullable=False, default='{}')
    request_id = db.Column(db.String(36), nullable=True, index=True)
    ip_address = db.Column(db.String(64), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    user = db.relationship('User')
