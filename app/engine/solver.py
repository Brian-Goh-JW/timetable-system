"""
CP-SAT timetable solver.
Assigns each ClassSession to a fixed (TimeSlot, Room) that repeats every teaching week.
"""

from collections import defaultdict
from datetime import timedelta, date
from ortools.sat.python import cp_model
import holidays as holidays_lib

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


def _get_sg_holidays(start_date, end_date):
    """Return a set of SG public holiday dates covering the given date range."""
    years = set(range(start_date.year, end_date.year + 1))
    sg_ph = holidays_lib.Singapore(years=years)
    return set(sg_ph.keys())


def _get_or_create_calendar(trimester, start_date, term_break_weeks=None):
    """Seed AcademicCalendar rows for the trimester if they don't exist yet.

    Args:
        term_break_weeks: set of week numbers to mark as term breaks.
                          Defaults to {7} if not provided.
    """
    if term_break_weeks is None:
        term_break_weeks = {7}

    existing = AcademicCalendar.query.filter_by(trimester=trimester).first()
    if not existing:
        trimester_end = start_date + timedelta(weeks=13)
        sg_holidays   = _get_sg_holidays(start_date, trimester_end)

        current = start_date
        for week_num in range(1, 14):
            is_break = week_num in term_break_weeks
            # Check if any day Mon–Fri of this week is a SG public holiday
            week_days  = [current + timedelta(days=d) for d in range(5)]
            ph_days    = [d for d in week_days if d in sg_holidays]
            is_ph      = len(ph_days) > 0
            ph_names   = ', '.join(
                holidays_lib.Singapore(years={d.year}).get(d, '') for d in ph_days
            )

            notes = []
            if is_break: notes.append('Term break')
            if is_ph:    notes.append(f'Public holiday: {ph_names}')

            db.session.add(AcademicCalendar(
                trimester        = trimester,
                week_number      = week_num,
                start_date       = current,
                end_date         = current + timedelta(days=4),
                is_term_break    = is_break,
                is_public_holiday= is_ph,
                notes            = '; '.join(notes) if notes else None,
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

def solve(trimester, start_date, term_break_weeks=None, trimester_num=None, academic_year=None,
          pinned_slots=None, historical_preferred=None):
    """
    Run CP-SAT to schedule all ready ClassSessions for the given trimester.

    Args:
        trimester         : str            — internal key, e.g. 'AY2526-T1'
        start_date        : date           — Monday of Week 1
        term_break_weeks  : set[int]       — week numbers to mark as term breaks (default: {7})
        trimester_num     : int|None       — 1, 2, or 3 — filters sessions by ClassSession.trimester
        academic_year     : str|None       — e.g. 'AY2526' — tagged onto each TimetableEntry
        pinned_slots         : dict[int, int]          — {session_id: timeslot_id} to preserve from last run
                                                         (Option A partial re-generation — ignored if strictly blocked)
        historical_preferred : dict[(str,str), int]    — {(module_code, session_type): timeslot_id}
                                                         soft preference to follow previous year's slot pattern

    Returns:
        (success: bool, message: str, stats: dict)
    """
    if term_break_weeks is None:
        term_break_weeks = {7}

    # 1. Load sessions that are ready:
    #    - f2f sessions need both professor and student group
    #    - online sessions only need a professor (no room or group required)
    from sqlalchemy import or_
    from app.models.class_session_professor import ClassSessionProfessor
    # A session is ready if it has at least one professor assigned
    ready_session_ids = db.session.query(ClassSessionProfessor.session_id).distinct().subquery()
    filters = [
        ClassSession.id.in_(ready_session_ids),
        or_(
            ClassSession.delivery_mode == 'online',
            ClassSession.student_group_id.isnot(None),
        ),
    ]
    # Filter by trimester number if specified (1, 2, or 3)
    if trimester_num is not None:
        filters.append(ClassSession.trimester == trimester_num)

    sessions = ClassSession.query.filter(*filters).all()

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
        for prof_id in s.all_professor_ids:
            if prof_id in strict_blocked:
                remaining = [
                    idx for idx in compat_slots[s.id]
                    if idx not in strict_blocked[prof_id]
                ]
                if not remaining:
                    prof = next((a.professor for a in s.professor_assignments if a.professor_id == prof_id), None)
                    ts_labels = ', '.join(
                        f'{timeslots[i].day_of_week} {timeslots[i].period_label}'
                        for i in strict_blocked[prof_id]
                        if i in compat_slots[s.id]
                    )
                    return False, (
                        f'Strict availability declarations for {prof.user.name if prof else prof_id} '
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

    # Hard constraint A — no professor double-booking (all professors incl. co-teachers)
    by_prof = defaultdict(list)
    for s in sessions:
        for prof_id in s.all_professor_ids:
            by_prof[prof_id].append(s)
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
    # Priority: fixed_timeslot_id (admin-set permanent pin) > pinned_slots (Option A carry-over)
    # Safety: never pin to a slot that is strictly blocked for any professor — that would make
    # the model infeasible.  Instead, quietly drop the pin so the solver can find a new slot.
    pins_applied    = 0
    pins_dropped    = 0
    for s in sessions:
        pinned_idx = None

        if s.fixed_timeslot_id:
            # Permanent admin pin
            pinned_idx = ts_id_to_index.get(s.fixed_timeslot_id)
        elif pinned_slots and s.id in pinned_slots:
            # Option A carry-over from previous run
            pinned_idx = ts_id_to_index.get(pinned_slots[s.id])

        if pinned_idx is None or pinned_idx not in compat_slots[s.id]:
            continue

        # Drop pin silently if any professor has a strict block on that slot
        is_strictly_blocked = any(
            pinned_idx in strict_blocked.get(prof_id, set())
            for prof_id in s.all_professor_ids
        )
        if is_strictly_blocked:
            pins_dropped += 1
            continue

        model.Add(slot_vars[s.id] == pinned_idx)
        pins_applied += 1

    # Hard constraint E — strict availability declarations (all professors)
    strict_constraints_added = 0
    for s in sessions:
        blocked_indices = set()
        for prof_id in s.all_professor_ids:
            blocked_indices |= strict_blocked.get(prof_id, set())
        for blocked_idx in blocked_indices:
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
        avoided_indices = set()
        for prof_id in s.all_professor_ids:
            avoided_indices |= preferred_avoided.get(prof_id, set())
        for avoid_idx in avoided_indices:
            if avoid_idx in compat_slots[s.id]:
                is_violated = model.NewBoolVar(f'pref_viol_{s.id}_{avoid_idx}')
                model.Add(slot_vars[s.id] == avoid_idx).OnlyEnforceIf(is_violated)
                model.Add(slot_vars[s.id] != avoid_idx).OnlyEnforceIf(is_violated.Not())
                ts_id = timeslots[avoid_idx].id
                # Use primary professor for declaration lookup
                decl  = pref_decl_map.get((s.primary_professor_id, ts_id))
                penalty_vars.append((is_violated, s, avoid_idx, decl))

    # Soft constraint G — day spread
    # Penalise pairs of sessions that land on the same day to prevent
    # Mon–Wed clustering when no availability declarations exist.
    # Weight (1) is far lower than preferred violation weight (100), so
    # spreading never overrides availability preference satisfaction.
    _day_num    = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4}
    slot_to_day = [_day_num.get(ts.day_of_week, 5) for ts in timeslots]

    day_var_map = {}
    for s in sessions:
        dv = model.NewIntVar(0, 5, f'day_{s.id}')
        model.AddElement(slot_vars[s.id], slot_to_day, dv)
        day_var_map[s.id] = dv

    spread_cost_vars = []
    for i in range(len(sessions)):
        for j in range(i + 1, len(sessions)):
            si, sj = sessions[i], sessions[j]
            same_day_b = model.NewBoolVar(f'sd_{si.id}_{sj.id}')
            model.Add(day_var_map[si.id] == day_var_map[sj.id]).OnlyEnforceIf(same_day_b)
            model.Add(day_var_map[si.id] != day_var_map[sj.id]).OnlyEnforceIf(same_day_b.Not())
            spread_cost_vars.append(same_day_b)

    # Soft constraint H — historical slot preference
    # When generating a new AY, prefer the same timeslot that was used in the previous year's
    # equivalent trimester (e.g. AY2627-T1 prefers AY2526-T1 slots).
    # Weight = 10: stronger than day-spread (1) but weaker than availability declarations (100).
    hist_penalty_vars = []   # BoolVar: 1 = session NOT in historical slot
    if historical_preferred:
        for s in sessions:
            key = (s.course.module_code.upper(), s.session_type)
            hist_ts_id = historical_preferred.get(key)
            if hist_ts_id is None:
                continue
            hist_idx = ts_id_to_index.get(hist_ts_id)
            if hist_idx is None or hist_idx not in compat_slots[s.id]:
                continue
            # Don't encourage a historically-preferred slot that is now strictly blocked
            is_strictly_blocked = any(
                hist_idx in strict_blocked.get(prof_id, set())
                for prof_id in s.all_professor_ids
            )
            if is_strictly_blocked:
                continue
            not_hist = model.NewBoolVar(f'not_hist_{s.id}')
            model.Add(slot_vars[s.id] != hist_idx).OnlyEnforceIf(not_hist)
            model.Add(slot_vars[s.id] == hist_idx).OnlyEnforceIf(not_hist.Not())
            hist_penalty_vars.append((not_hist, s, hist_idx))

    # Combined objective:
    #   availability violations  → weight 100 (highest priority)
    #   historical slot changes  → weight 10  (strong preference for continuity)
    #   day spread penalty       → weight 1   (tiebreaker)
    obj_terms = []
    for pv, *_ in penalty_vars:
        obj_terms.append(100 * pv)
    for not_hist, *_ in hist_penalty_vars:
        obj_terms.append(10 * not_hist)
    for sv in spread_cost_vars:
        obj_terms.append(sv)

    if obj_terms:
        model.Minimize(sum(obj_terms))

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
            primary = s.primary_professor
        preferred_violations.append({
                'professor'      : primary.user.name if primary else '?',
                'staff_id'       : primary.staff_id  if primary else '?',
                'professor_id'   : s.primary_professor_id,
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

    cal_weeks = _get_or_create_calendar(trimester, start_date, term_break_weeks)
    # Only skip full term breaks — public holiday weeks are handled per-session-day below
    non_break_weeks = [w for w in cal_weeks if not w.is_term_break]

    # Pre-compute SG holidays for precise per-day check
    if non_break_weeks:
        ph_dates = _get_sg_holidays(non_break_weeks[0].start_date,
                                    non_break_weeks[-1].end_date)
    else:
        ph_dates = set()

    # Load events for this trimester (cancel-outcome events block dates)
    from app.models.event import Event
    event_filters = [Event.outcome == 'cancel']
    if trimester_num is not None:
        # Match events with no trimester set (school-wide) OR events matching this trimester
        event_filters.append(
            db.or_(Event.trimester == None, Event.trimester == trimester_num)
        )
    if academic_year is not None:
        # Match events with no AY set OR events matching this AY
        event_filters.append(
            db.or_(Event.academic_year == None, Event.academic_year == academic_year)
        )
    cancel_events = Event.query.filter(*event_filters).all()
    # Build: date -> set of blocked timeslot_ids (empty set = full day block)
    event_blocks = {}  # date -> set of ts_ids (empty = full day)
    for ev in cancel_events:
        if ev.event_date not in event_blocks:
            event_blocks[ev.event_date] = set() if ev.is_full_day else set(ev.blocked_timeslot_ids)
        else:
            if ev.is_full_day:
                event_blocks[ev.event_date] = set()  # full day override
            elif event_blocks[ev.event_date]:  # only add if not already full day
                event_blocks[ev.event_date].update(ev.blocked_timeslot_ids)

    _day_offset = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4}

    entries = 0
    skipped_ph = 0
    skipped_ev = 0
    for s in sessions:
        ts      = timeslots[cp_solver.Value(slot_vars[s.id])]
        room_id = None
        if s.delivery_mode == 'f2f' and s.id in room_vars:
            room_id = rooms[cp_solver.Value(room_vars[s.id])].id

        day_offset = _day_offset.get(ts.day_of_week, 0)

        for week in non_break_weeks:
            session_date = week.start_date + timedelta(days=day_offset)

            # Skip public holidays
            if session_date in ph_dates:
                skipped_ph += 1
                continue

            # Skip event-blocked dates
            if session_date in event_blocks:
                blocked_ts = event_blocks[session_date]
                if not blocked_ts or ts.id in blocked_ts:  # full day or specific slot
                    skipped_ev += 1
                    continue

            db.session.add(TimetableEntry(
                class_session_id  = s.id,
                timeslot_id       = ts.id,
                room_id           = room_id,
                week_number       = week.week_number,
                trimester         = trimester,
                academic_year     = academic_year,
                is_published      = False,
                is_manually_edited= False,
            ))
            entries += 1

    db.session.commit()

    return True, 'Timetable generated successfully.', {
        'sessions_scheduled'        : len(sessions),
        'teaching_weeks'            : len(non_break_weeks),
        'entries_created'           : entries,
        'entries_skipped_ph'        : skipped_ph,
        'entries_skipped_events'    : skipped_ev,
        'solver_status'             : 'Optimal' if status == cp_model.OPTIMAL else 'Feasible',
        'strict_constraints_applied': strict_constraints_added,
        'preferred_violations'      : preferred_violations,
        'pins_applied'              : pins_applied,
        'pins_dropped'              : pins_dropped,
        'historical_honoured'       : sum(1 for nh, *_ in hist_penalty_vars if cp_solver.Value(nh) == 0),
        'historical_changed'        : sum(1 for nh, *_ in hist_penalty_vars if cp_solver.Value(nh) == 1),
    }
