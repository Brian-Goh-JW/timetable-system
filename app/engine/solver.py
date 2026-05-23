"""
CP-SAT timetable solver.
Assigns each ClassSession to a fixed (TimeSlot, Room) that repeats every teaching week.
"""

from collections import defaultdict
from datetime import timedelta
from ortools.sat.python import cp_model

from app import db
from app.models.class_session import ClassSession
from app.models.timeslot import TimeSlot
from app.models.room import Room
from app.models.student_group import StudentGroup
from app.models.academic_calendar import AcademicCalendar
from app.models.timetable_entry import TimetableEntry
from app.models.availability_declaration import AvailabilityDeclaration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slot_compatible(timeslot, session):
    """A timeslot is compatible if duration matches and lab/non-lab type aligns."""
    start_mins = timeslot.start_time.hour * 60 + timeslot.start_time.minute
    end_mins   = timeslot.end_time.hour   * 60 + timeslot.end_time.minute
    slot_hours = (end_mins - start_mins) // 60

    if slot_hours != session.duration_hours:
        return False

    is_lab_slot    = timeslot.period_label.startswith('Lab')
    is_lab_session = (session.session_type == 'lab')
    return is_lab_slot == is_lab_session


def _room_compatible(room, session):
    """Room type must match session type and capacity must fit the student group."""
    if session.session_type == 'lab':
        if room.room_type != 'lab':
            return False
    elif session.session_type == 'lecture':
        if room.room_type not in ('lecture', 'seminar'):
            return False
    else:   # tutorial, seminar
        if room.room_type not in ('seminar', 'lecture'):
            return False

    group_size = session.student_group.intake_size if session.student_group else 1
    return room.capacity >= group_size


def _conflicting_group_ids(group_id):
    """
    Return the set of student-group IDs that cannot share a timeslot with group_id.
    Includes the group itself, its parent (if any), and all its sub-groups.
    """
    group = StudentGroup.query.get(group_id)
    if not group:
        return {group_id}

    ids = {group_id}
    if group.parent_id:
        ids.add(group.parent_id)
    for sub in group.sub_groups:
        ids.add(sub.id)
    return ids


def _get_or_create_calendar(trimester, start_date):
    """Seed AcademicCalendar rows for the trimester if they don't exist yet."""
    existing = AcademicCalendar.query.filter_by(trimester=trimester).first()
    if not existing:
        current = start_date
        for week_num in range(1, 14):
            db.session.add(AcademicCalendar(
                trimester=trimester,
                week_number=week_num,
                start_date=current,
                end_date=current + timedelta(days=4),
                is_term_break=(week_num == 7),
                is_public_holiday=False,
                notes='Term break' if week_num == 7 else None,
            ))
            current += timedelta(weeks=1)
        db.session.commit()

    return (AcademicCalendar.query
            .filter_by(trimester=trimester)
            .order_by(AcademicCalendar.week_number)
            .all())


# ---------------------------------------------------------------------------
# Main solver
# ---------------------------------------------------------------------------

def solve(trimester, start_date):
    """
    Run CP-SAT to schedule all ready ClassSessions for the given trimester.

    Args:
        trimester  : str  — e.g. '2025-T3'
        start_date : date — Monday of Week 1

    Returns:
        (success: bool, message: str, stats: dict)
    """

    # 1. Load sessions that are ready:
    #    - f2f sessions need both professor and student group
    #    - online sessions only need a professor (no room or group required)
    from sqlalchemy import or_
    sessions = (ClassSession.query
                .filter(
                    ClassSession.professor_id.isnot(None),
                    or_(
                        ClassSession.delivery_mode == 'online',
                        ClassSession.student_group_id.isnot(None),
                    )
                )
                .all())

    if not sessions:
        return False, 'No sessions are ready to schedule. Assign professors and student groups first.', {}

    timeslots = (TimeSlot.query
                 .order_by(TimeSlot.day_of_week, TimeSlot.period_label)
                 .all())
    rooms = Room.query.filter_by(is_active=True).order_by(Room.room_code).all()

    # Build a fast lookup: timeslot.id → index in `timeslots` list
    ts_id_to_index = {ts.id: i for i, ts in enumerate(timeslots)}

    # 2. Build compatibility maps
    compat_slots = {}   # session.id → [timeslot indices]
    for s in sessions:
        compat = [i for i, ts in enumerate(timeslots) if _slot_compatible(ts, s)]
        if not compat:
            return False, (
                f'No compatible time slots for {s.course.module_code} '
                f'({s.session_type}, {s.duration_hours}h). '
                f'Check session type and duration.'
            ), {}
        compat_slots[s.id] = compat

    compat_rooms = {}   # session.id → [room indices]  (f2f only)
    for s in sessions:
        if s.delivery_mode == 'f2f':
            compat = [i for i, r in enumerate(rooms) if _room_compatible(r, s)]
            if not compat:
                group_size = s.student_group.intake_size if s.student_group else '?'
                return False, (
                    f'No compatible rooms for {s.course.module_code} '
                    f'({s.session_type}, needs capacity {group_size}). '
                    f'Check room availability and capacity.'
                ), {}
            compat_rooms[s.id] = compat

    # 3. Load classified availability declarations
    #    strict  → hard block (solver must never place professor here)
    #    preferred → soft avoid (solver penalises but may use if necessary)

    strict_decls    = AvailabilityDeclaration.query.filter_by(
        status='classified', constraint_type='strict'
    ).all()
    preferred_decls = AvailabilityDeclaration.query.filter_by(
        status='classified', constraint_type='preferred'
    ).all()

    # professor_id → set of timeslot indices that are hard-blocked
    strict_blocked = defaultdict(set)
    for d in strict_decls:
        idx = ts_id_to_index.get(d.timeslot_id)
        if idx is not None:
            strict_blocked[d.professor_id].add(idx)

    # professor_id → set of timeslot indices that are soft-avoided
    preferred_avoided = defaultdict(set)
    for d in preferred_decls:
        idx = ts_id_to_index.get(d.timeslot_id)
        if idx is not None:
            preferred_avoided[d.professor_id].add(idx)

    # Pre-check: if strict declarations block ALL compatible slots for a session,
    # the problem is immediately infeasible — catch it early with a clear message.
    for s in sessions:
        if s.professor_id in strict_blocked:
            remaining = [
                idx for idx in compat_slots[s.id]
                if idx not in strict_blocked[s.professor_id]
            ]
            if not remaining:
                ts_labels = ', '.join(
                    f'{timeslots[i].day_of_week} {timeslots[i].period_label}'
                    for i in strict_blocked[s.professor_id]
                    if i in compat_slots[s.id]
                )
                return False, (
                    f'Strict availability declarations for {s.professor.user.name} '
                    f'block all compatible slots for {s.course.module_code} '
                    f'({s.session_type}). Blocked: {ts_labels}. '
                    f'Review declarations or reassign the session.'
                ), {}

    # 4. Build CP-SAT model
    model = cp_model.CpModel()

    slot_vars = {
        s.id: model.NewIntVarFromDomain(
            cp_model.Domain.FromValues(compat_slots[s.id]),
            f'slot_{s.id}'
        )
        for s in sessions
    }

    room_vars = {
        s.id: model.NewIntVarFromDomain(
            cp_model.Domain.FromValues(compat_rooms[s.id]),
            f'room_{s.id}'
        )
        for s in sessions if s.delivery_mode == 'f2f'
    }

    # Hard constraint A — no professor double-booking
    by_prof = defaultdict(list)
    for s in sessions:
        by_prof[s.professor_id].append(s)
    for sess_list in by_prof.values():
        if len(sess_list) > 1:
            model.AddAllDifferent([slot_vars[s.id] for s in sess_list])

    # Hard constraint B — no student-group double-booking (parent/child aware)
    for i in range(len(sessions)):
        for j in range(i + 1, len(sessions)):
            si, sj = sessions[i], sessions[j]
            if si.student_group_id and sj.student_group_id:
                if sj.student_group_id in _conflicting_group_ids(si.student_group_id):
                    model.Add(slot_vars[si.id] != slot_vars[sj.id])

    # Hard constraint C — no room double-booking
    f2f = [s for s in sessions if s.delivery_mode == 'f2f']
    for i in range(len(f2f)):
        for j in range(i + 1, len(f2f)):
            si, sj = f2f[i], f2f[j]
            same = model.NewBoolVar(f'same_{si.id}_{sj.id}')
            model.Add(slot_vars[si.id] == slot_vars[sj.id]).OnlyEnforceIf(same)
            model.Add(slot_vars[si.id] != slot_vars[sj.id]).OnlyEnforceIf(same.Not())
            model.Add(room_vars[si.id] != room_vars[sj.id]).OnlyEnforceIf(same)

    # Hard constraint D — fixed timeslot pins
    # If a session has fixed_timeslot_id set, the solver must assign it to exactly that slot.
    for s in sessions:
        if s.fixed_timeslot_id:
            fixed_idx = ts_id_to_index.get(s.fixed_timeslot_id)
            if fixed_idx is not None and fixed_idx in compat_slots[s.id]:
                model.Add(slot_vars[s.id] == fixed_idx)

    # Hard constraint E — strict availability declarations
    # For every session whose professor has a strict block on a timeslot,
    # forbid that timeslot for the session (only if it was compatible to begin with).
    strict_constraints_added = 0
    for s in sessions:
        for blocked_idx in strict_blocked.get(s.professor_id, set()):
            if blocked_idx in compat_slots[s.id]:
                model.Add(slot_vars[s.id] != blocked_idx)
                strict_constraints_added += 1

    # Soft constraint F — preferred availability declarations
    # Create a boolean penalty variable for each (session, avoided_slot) pair.
    # penalty_var = 1 means the solver placed the session in an avoided slot.
    # Objective: minimise total penalties.
    penalty_vars   = []   # (bool_var, session, timeslot_index, declaration)
    pref_decl_map  = {    # (professor_id, timeslot_id) → declaration object
        (d.professor_id, d.timeslot_id): d for d in preferred_decls
    }

    for s in sessions:
        for avoid_idx in preferred_avoided.get(s.professor_id, set()):
            if avoid_idx in compat_slots[s.id]:
                is_violated = model.NewBoolVar(f'pref_viol_{s.id}_{avoid_idx}')
                model.Add(slot_vars[s.id] == avoid_idx).OnlyEnforceIf(is_violated)
                model.Add(slot_vars[s.id] != avoid_idx).OnlyEnforceIf(is_violated.Not())
                ts_id = timeslots[avoid_idx].id
                decl  = pref_decl_map.get((s.professor_id, ts_id))
                penalty_vars.append((is_violated, s, avoid_idx, decl))

    # Only set an objective if there are soft constraints to optimise
    if penalty_vars:
        model.Minimize(sum(pv for pv, *_ in penalty_vars))

    # 5. Solve
    cp_solver = cp_model.CpSolver()
    cp_solver.parameters.max_time_in_seconds = 60
    cp_solver.parameters.num_search_workers  = 4

    status = cp_solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return False, (
            'CP-SAT could not find a feasible timetable. '
            'This may be caused by strict availability declarations blocking too many slots, '
            'or too many sessions competing for limited timeslots. '
            'Check the Declarations page and session assignments.'
        ), {}

    # 6. Detect preferred violations (slots where solver had to override a preference)
    preferred_violations = []
    for is_violated, s, avoid_idx, decl in penalty_vars:
        if cp_solver.Value(is_violated) == 1:
            ts = timeslots[avoid_idx]
            preferred_violations.append({
                'professor'      : s.professor.user.name,
                'staff_id'       : s.professor.staff_id,
                'professor_id'   : s.professor_id,
                'module_code'    : s.course.module_code,
                'module_title'   : s.course.title,
                'session_type'   : s.session_type,
                'day'            : ts.day_of_week,
                'period'         : ts.period_label,
                'time'           : f'{ts.start_time.strftime("%H:%M")}–{ts.end_time.strftime("%H:%M")}',
                'timeslot_id'    : ts.id,
                'declaration_id' : decl.id if decl else None,
                'class_session_id': s.id,
            })

    # 7. Write TimetableEntry records
    TimetableEntry.query.filter_by(trimester=trimester).delete()

    cal_weeks      = _get_or_create_calendar(trimester, start_date)
    teaching_weeks = [w for w in cal_weeks if not w.is_term_break and not w.is_public_holiday]

    entries = 0
    for s in sessions:
        ts      = timeslots[cp_solver.Value(slot_vars[s.id])]
        room_id = None
        if s.delivery_mode == 'f2f' and s.id in room_vars:
            room_id = rooms[cp_solver.Value(room_vars[s.id])].id

        for week in teaching_weeks:
            db.session.add(TimetableEntry(
                class_session_id  = s.id,
                timeslot_id       = ts.id,
                room_id           = room_id,
                week_number       = week.week_number,
                trimester         = trimester,
                is_published      = False,
                is_manually_edited= False,
            ))
            entries += 1

    db.session.commit()

    return True, 'Timetable generated successfully.', {
        'sessions_scheduled'        : len(sessions),
        'teaching_weeks'            : len(teaching_weeks),
        'entries_created'           : entries,
        'solver_status'             : 'Optimal' if status == cp_model.OPTIMAL else 'Feasible',
        'strict_constraints_applied': strict_constraints_added,
        'preferred_violations'      : preferred_violations,
    }
