from app import db


class ClassSession(db.Model):
    __tablename__ = 'class_sessions'

    id                = db.Column(db.Integer, primary_key=True)
    course_id         = db.Column(db.Integer, db.ForeignKey('courses.id'),         nullable=False)
    session_type          = db.Column(db.Enum('lecture', 'lab', 'seminar', 'tutorial',
                                              'lectorial', 'workshop', 'quiz'),      nullable=False)
    delivery_mode         = db.Column(db.Enum('f2f', 'online'),                      nullable=False)
    is_async              = db.Column(db.Boolean, nullable=False, default=False)      # True = online async, no timeslot needed
    duration_hours        = db.Column(db.Integer,                                     nullable=False)
    student_group_id      = db.Column(db.Integer, db.ForeignKey('student_groups.id'), nullable=True)
    fixed_timeslot_id     = db.Column(db.Integer, db.ForeignKey('timeslots.id'),      nullable=True)
    preferred_timeslot_id = db.Column(db.Integer, db.ForeignKey('timeslots.id'),      nullable=True)
    fixed_room_id         = db.Column(db.Integer, db.ForeignKey('rooms.id'),          nullable=True)
    # ^ locks this session to one specific room (the solver still picks the time),
    # set when the source data names an exact venue - see bootstrap/34.
    trimester             = db.Column(db.Integer,                                      nullable=True)  # 1, 2, or 3
    teaching_weeks        = db.Column(db.String(100), nullable=True)                  # e.g. "1,2,3,4,5,6,8,9,10,11,12,13"
    group_label           = db.Column(db.String(20),  nullable=True)                  # Template 2 group: "All", "T1", "L1", "P1"
    shared_module_group_id = db.Column(db.Integer, db.ForeignKey('shared_module_groups.id'), nullable=True)
    # ^ set when this session must be scheduled together with other programmes'
    # sessions for "the same" module (see SharedModuleGroup) - same room+slot, combined capacity.
    deferred_from_solve = db.Column(db.Boolean, nullable=False, default=False)
    required_equipment = db.Column(db.Text, nullable=True)
    accessibility_required = db.Column(db.Boolean, nullable=False, default=False)
    required_qualification = db.Column(db.String(100), nullable=True)
    # ^ True = temporarily excluded from CP-SAT generation for this trimester
    # (see bootstrap/48) - not a data gap, a deliberate scope decision
    # disclosed on System Info, reversible by clearing the flag once a
    # follow-up generation pass covers this session's programme too.

    @property
    def required_equipment_tags(self):
        return {
            tag.strip().lower()
            for tag in (self.required_equipment or '').split(',')
            if tag.strip()
        }

    course             = db.relationship('Course',    backref='class_sessions')
    student_group      = db.relationship('StudentGroup', backref='class_sessions')
    fixed_timeslot     = db.relationship('TimeSlot',  foreign_keys=[fixed_timeslot_id])
    preferred_timeslot = db.relationship('TimeSlot',  foreign_keys=[preferred_timeslot_id])
    fixed_room         = db.relationship('Room',      foreign_keys=[fixed_room_id])
    shared_module_group = db.relationship('SharedModuleGroup', backref='class_sessions')

    # All professor assignments - ordered by display_order (0 = primary)
    professor_assignments = db.relationship(
        'ClassSessionProfessor',
        backref='class_session',
        cascade='all, delete-orphan',
        order_by='ClassSessionProfessor.display_order',
    )

    # -----------------------------------------------------------------------
    # Convenience helpers
    # -----------------------------------------------------------------------

    @property
    def primary_professor(self):
        """Primary Professor object, or None."""
        for a in self.professor_assignments:
            if a.is_primary:
                return a.professor
        return self.professor_assignments[0].professor if self.professor_assignments else None

    @property
    def primary_professor_id(self):
        p = self.primary_professor
        return p.id if p else None

    @property
    def all_professors(self):
        """List of all Professor objects for this session."""
        return [a.professor for a in self.professor_assignments]

    @property
    def all_professor_ids(self):
        return [a.professor_id for a in self.professor_assignments]

    @property
    def effective_group_size(self):
        """The real number of students in THIS session, correcting for
        StudentGroup.intake_size representing the whole cohort even when a
        class is split into several simultaneous parallel sections
        (group_label "P1"/"P2", "L1"/"L2", "T1"/"T2", ...) sharing that one
        StudentGroup - a cleaned-data import limitation (one intake_size per
        programme-year, not per session) that made every parallel section
        claim the full cohort. Found 2026-07-18 cross-checking a Template 2
        export's Class Size against real room capacities.

        Only kicks in for a "<letter><n>" label (any of GROUP_LABEL_PREFIX's
        letters, not just lab's "P" - e.g. MET1101's lectorial split is
        "L1"/"L2") with at least one OTHER same-lettered sibling (same course
        + session_type + student_group) whose teaching weeks actually
        overlap this session's - confirming they run in parallel, not
        sequentially across the trimester. This is the important guard: some
        "L1"/"L2" pairs (e.g. ASE1011's lecture) are the SAME cohort meeting
        at two different points in the term, never simultaneously - each of
        those genuinely is the full cohort, not a capacity split, and
        _weeks_overlap correctly returns False for that pair so this leaves
        them unchanged. Every unsplit group_label ("All") is unaffected."""
        if not self.student_group:
            return None
        import re
        if not self.group_label or not re.match(r'^[A-Z]\d+$', self.group_label):
            return self.student_group.intake_size
        from app.engine.solver import _parallel_section_alternatives
        siblings = ClassSession.query.filter_by(
            course_id=self.course_id, session_type=self.session_type,
            student_group_id=self.student_group_id,
        ).all()
        # Count the connected parallel-section component.  This keeps a
        # sequential P1/P2 pair at full cohort size while giving every member
        # of a true P1/P2/P3 parallel set the same section capacity.
        concurrent = [self]
        pending = [self]
        while pending:
            current = pending.pop()
            for sibling in siblings:
                if (sibling not in concurrent
                        and _parallel_section_alternatives(current, sibling)):
                    concurrent.append(sibling)
                    pending.append(sibling)
        if len(concurrent) < 2:
            return self.student_group.intake_size
        import math
        return math.ceil(self.student_group.intake_size / len(concurrent))

    def __repr__(self):
        code = self.course.module_code if self.course else '?'
        return f'<ClassSession {code} {self.session_type} ({self.delivery_mode})>'
