from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class SolverJob(db.Model):
    """Persistent generation job state shared by every web/worker process."""

    __tablename__ = 'solver_jobs'

    id = db.Column(db.String(36), primary_key=True)
    academic_year = db.Column(db.String(10), nullable=False, index=True)
    trimester = db.Column(db.String(20), nullable=False, index=True)
    action = db.Column(db.String(30), nullable=False, default='generate')
    status = db.Column(db.String(20), nullable=False, default='queued', index=True)
    progress_message = db.Column(db.String(500), nullable=True)
    success = db.Column(db.Boolean, nullable=True)
    payload_json = db.Column(db.Text, nullable=False, default='{}')
    stats_json = db.Column(db.Text, nullable=False, default='{}')
    error_message = db.Column(db.String(500), nullable=True)
    requested_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    cancel_requested = db.Column(db.Boolean, nullable=False, default=False)
    # A unique non-NULL key is held only while queued/running.  It is cleared
    # when the job finishes, preventing duplicate cross-process generations.
    active_key = db.Column(db.String(20), unique=True, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    heartbeat_at = db.Column(db.DateTime(timezone=True), nullable=True)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)

    requester = db.relationship('User')
