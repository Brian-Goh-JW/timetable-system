from datetime import datetime, timezone
from app import db


class SolverSetting(db.Model):
    """Admin-editable override for one soft constraint's on/off state and
    priority weight (e.g. 'S9' - prefer sessions end by 5pm). No row for a
    given constraint_id means "use solver.py's default weight, enabled" -
    a row only exists once an admin has actually changed something.

    Disabling a constraint keeps its stored weight_override untouched and
    just forces the solver's effective weight to 0 for that run, rather than
    deleting the row - so a temporarily-disabled priority isn't lost.
    """
    __tablename__ = 'solver_settings'

    id              = db.Column(db.Integer, primary_key=True)
    constraint_id   = db.Column(db.String(20), unique=True, nullable=False)   # e.g. 'S9', 'S-avail'
    enabled         = db.Column(db.Boolean, nullable=False, default=True)
    weight_override = db.Column(db.Integer, nullable=True)   # None = use solver.py's default
    updated_at      = db.Column(db.DateTime, nullable=False,
                                 default=lambda: datetime.now(timezone.utc),
                                 onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<SolverSetting {self.constraint_id} enabled={self.enabled} weight={self.weight_override}>'
