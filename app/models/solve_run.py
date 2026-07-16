from datetime import datetime, timezone
from app import db


class SolveRun(db.Model):
    """The most recent successful solve() outcome for one trimester key
    (e.g. 'AY2526-T1'). One row per trimester - upserted on every successful
    generation so the constraint summary and scheduling report survive
    server restarts and don't depend on which process ran the solve.
    """
    __tablename__ = 'solve_runs'

    id            = db.Column(db.Integer, primary_key=True)
    trimester     = db.Column(db.String(20), unique=True, nullable=False)   # e.g. 'AY2526-T1'
    solver_status = db.Column(db.String(20), nullable=False)                # 'Optimal' | 'Feasible'
    stats_json    = db.Column(db.Text, nullable=False)                      # solve() stats dict, json-encoded
    created_at    = db.Column(db.DateTime, nullable=False,
                               default=lambda: datetime.now(timezone.utc))
    # When this row's stats were last (re)written - since the row is
    # upserted on every regenerate, created_at stays fixed at the FIRST
    # generation forever. Reports must show updated_at, not created_at, or
    # they display a stale date even though the numbers underneath are
    # current (found 2026-07-16).
    updated_at    = db.Column(db.DateTime, nullable=True,
                               default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<SolveRun {self.trimester} {self.solver_status}>'
