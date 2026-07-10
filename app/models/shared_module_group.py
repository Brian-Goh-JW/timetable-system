from app import db


class SharedModuleGroup(db.Model):
    """A module offered under different codes/numbering across multiple programmes
    that must be scheduled as ONE combined class - same room, same timeslot, all
    linked ClassSessions' student groups attending together.

    Sourced from 'Common modules.xlsx' (and the "Programme Grouping Requirements"
    examples in the requirements doc, e.g. SBE1109 combined with ESE) - not derived
    from any per-session field, so every link here is a self-input assumption.
    """
    __tablename__ = 'shared_module_groups'

    id          = db.Column(db.Integer, primary_key=True)
    label       = db.Column(db.String(100), nullable=False)   # raw source label, e.g. "ESE1101/SBE1101/ASE1011"
    year_level  = db.Column(db.Integer, nullable=True)
    remarks     = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f'<SharedModuleGroup {self.label}>'
