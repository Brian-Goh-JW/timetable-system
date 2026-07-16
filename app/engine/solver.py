"""
CP-SAT timetable solver.
Assigns each ClassSession to a fixed (TimeSlot, Room) that repeats every teaching week.
"""

from collections import defaultdict
from datetime import timedelta, date, time as dtime
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
# Constraint constants - single source of truth for solver.py AND the admin
# "System Info" constraints page (app/routes/admin.py: system_info route).
# ---------------------------------------------------------------------------

# Institutional time-block rules (hard)
WED_AFTERNOON_CUTOFF   = dtime(13, 0)   # Wed: no classes starting at/after this time
FRI_BLOCK_START        = dtime(12, 0)   # Fri: protected window start
FRI_BLOCK_END          = dtime(14, 0)   # Fri: protected window end (exclusive)
EVENING_CUTOFF         = dtime(18, 0)   # any day: no unpinned classes starting at/after this time
# Lunch is NOT a fixed block (corrected by Ms. Yang, 9 Jul 2026) - each student
# group just needs >=1 fully free hour somewhere in this window, any day.
LUNCH_WINDOW_START     = dtime(11, 0)
LUNCH_WINDOW_END       = dtime(14, 0)
DEFAULT_TERM_BREAK_WEEKS = {7}          # used when no term_break_weeks given

# Soft constraint objective weights - higher wins when constraints conflict
WEIGHT_AVAILABILITY    = 100   # strict availability declaration violated
WEIGHT_PREFERRED_TS    = 50    # Remarks-parsed preferred timeslot missed
WEIGHT_HISTORICAL      = 80    # different slot vs previous AY's equivalent trimester / real backbone
                                # (raised from 30 on 2026-07-10 - DSC's backbone is a real, already-
                                # committed timetable, not a soft guess, so it should only be
                                # outweighed by a hard-ish reason like an availability declaration)
WEIGHT_LATE_END        = 15    # session ends after LATE_END_CUTOFF
WEIGHT_MODE_SWITCH_PROF        = 8    # professor's Online<->F2F switch in adjacent slots
WEIGHT_MODE_SWITCH_GROUP       = 8    # student group's Online<->F2F switch in adjacent slots
WEIGHT_PROF_IDLE_GAP           = 6    # professor idle gap > PROF_IDLE_GAP_THRESHOLD_HOURS in a day
WEIGHT_GROUP_BACKTOBACK_HOURS  = 6    # group zero-gap run >= GROUP_BACKTOBACK_LIMIT_HOURS+1 hours
WEIGHT_LEC_TUT_ORDER   = 5     # lecture scheduled after its tutorial
WEIGHT_LEC_LAB_ORDER            = 5    # lecture scheduled after its lab (same tier as lec-tut)
WEIGHT_ROOM_UTIL                = 3    # f2f session in a room under ROOM_UTIL_THRESHOLD capacity
WEIGHT_ROOM_BEST_FIT            = 2    # f2f session in a room with wasted (unused) capacity
WEIGHT_CONSISTENT_VENUE         = 2    # adjacent-slot room change for the same professor/group
WEIGHT_EXTREMAL_SLOT   = 2     # group placed in first/last slot of the day
WEIGHT_DAY_CLUSTER     = 1     # group's sessions spread across more days than needed

LATE_END_CUTOFF        = dtime(17, 0)   # soft: prefer sessions end by this time
PROF_IDLE_GAP_THRESHOLD_HOURS  = 2      # soft: professor idle time between classes in a day
GROUP_BACKTOBACK_LIMIT_HOURS   = 2      # soft: zero-gap run reaching limit+1=3h is a violation
ROOM_UTIL_THRESHOLD            = 0.6    # soft: prefer group_size / room.capacity >= this
LONG_SESSION_THRESHOLD_HOURS   = 4      # visibility only: single session > this is flagged (not solver-enforced)

# Module codes with an explicit "N sessions/week, >=1 day apart" pattern requirement
# (per Ms. Yang's requirements doc - these two are the only ones named).
UNIWIDE_DAY_SEPARATED_MODULES = {'UCS1001', 'UDE2222'}

# Soft constraint id -> its default weight above. Single source of truth for
# get_effective_soft_weights() below and for admin.py's Scoring Matrix -
# every soft constraint the objective actually uses must have an entry here.
SOFT_CONSTRAINT_DEFAULTS = {
    'S-avail':   WEIGHT_AVAILABILITY,
    'S-pref-ts': WEIGHT_PREFERRED_TS,
    'S-hist':    WEIGHT_HISTORICAL,
    'S1':        WEIGHT_MODE_SWITCH_PROF,
    'S2':        WEIGHT_MODE_SWITCH_GROUP,
    'S3':        WEIGHT_PROF_IDLE_GAP,
    'S8':        WEIGHT_GROUP_BACKTOBACK_HOURS,
    'S5':        WEIGHT_DAY_CLUSTER,
    'S6':        WEIGHT_ROOM_UTIL,
    'S7':        WEIGHT_EXTREMAL_SLOT,
    'S9':        WEIGHT_LATE_END,
    'S10':       WEIGHT_ROOM_BEST_FIT,
    'S11':       WEIGHT_CONSISTENT_VENUE,
    'S-lec-tut': WEIGHT_LEC_TUT_ORDER,
    'S-lec-lab': WEIGHT_LEC_LAB_ORDER,
}


def get_effective_soft_weights():
    """Live per-constraint weight for this solve, honouring any admin
    override/on-off toggle stored in SolverSetting (Admin Tools > Constraint
    Settings). Falls back to SOFT_CONSTRAINT_DEFAULTS for anything with no
    row yet. A disabled constraint always resolves to 0 - since every one of
    these is a soft (objective-only) term, a weight of 0 is functionally
    identical to skipping it entirely, and keeps the stored weight_override
    intact for whenever it's re-enabled."""
    from app.models.solver_setting import SolverSetting
    weights = dict(SOFT_CONSTRAINT_DEFAULTS)
    for row in SolverSetting.query.all():
        if row.constraint_id not in weights:
            continue
        if not row.enabled:
            weights[row.constraint_id] = 0
        elif row.weight_override is not None:
            weights[row.constraint_id] = row.weight_override
    return weights


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _weeks_overlap(a, b):
    """Return True if sessions a and b share at least one teaching week.
    Sessions with non-overlapping week ranges can occupy the same timeslot without conflict.
    """
    if not a.teaching_weeks or not b.teaching_weeks:
        return True  # unknown weeks → assume overlap to be safe
    wa = set(int(w) for w in a.teaching_weeks.split(',') if w.strip().isdigit())
    wb = set(int(w) for w in b.teaching_weeks.split(',') if w.strip().isdigit())
    if not wa or not wb:
        return True
    return bool(wa & wb)


def _slot_compatible(timeslot, session):
    """A timeslot is compatible if its duration matches the session's required duration."""
    start_mins = timeslot.start_time.hour * 60 + timeslot.start_time.minute
    end_mins   = timeslot.end_time.hour   * 60 + timeslot.end_time.minute
    slot_hours = (end_mins - start_mins) // 60
    return slot_hours == session.duration_hours


def _room_compatible(room, session, group_size_override=None, require_capacity=True):
    """Room type must match session type and (unless require_capacity=False)
    capacity must fit the student group. group_size_override lets callers
    substitute the combined enrollment for sessions linked via a
    SharedModuleGroup (see solve()) instead of this session's own
    student_group size.

    require_capacity=False is for explicitly-pinned rooms (fixed_room_id):
    the group's full intake_size often overstates the real room need, since
    labs/tutorials are commonly split into several smaller parallel groups
    that aren't yet modelled as separate sessions - a human-confirmed real
    room assignment is more trustworthy here than the capacity heuristic."""
    LARGE_ROOM_TYPES = ('lecture', 'lectorial', 'quiz')
    if session.session_type == 'lab':
        if room.room_type != 'lab':
            return False
    elif session.session_type in LARGE_ROOM_TYPES:
        if room.room_type not in ('lecture', 'seminar'):
            return False
    else:   # tutorial, seminar, workshop
        if room.room_type not in ('seminar', 'lecture'):
            return False

    if not require_capacity:
        return True

    if group_size_override is not None:
        group_size = group_size_override
    else:
        group_size = session.student_group.intake_size if session.student_group else 1
    return room.capacity >= group_size


def _institutional_blocked_indices(timeslots):
    """
    Indices of timeslots blocked by SIT institutional policy:
      - Wednesday:   any slot starting at or after 13:00 (CCA afternoon policy)
      - Friday:      any slot starting at or after 12:00 and before 14:00
      - Any day:     any slot starting at or after 18:00 (no evening classes)
    Lunch is NOT a fixed block (corrected by Ms. Yang) - each group just needs
    >=1 free hour somewhere in [11:00, 14:00), enforced separately by the
    lunch-window hard constraint in solve() using LUNCH_HOURS.
    Fixed sessions (fixed_timeslot_id) bypass this check - added 2026-07-11 so
    a handful of real, source-confirmed 6-8pm DSC-shared-module quizzes could
    be added to the TimeSlot catalog (needed to reproduce them at all) without
    opening evening slots up to every other, unpinned session too.
    """
    blocked = set()
    for i, ts in enumerate(timeslots):
        if ts.day_of_week == 'Wednesday' and ts.start_time >= WED_AFTERNOON_CUTOFF:
            blocked.add(i)
        if ts.day_of_week == 'Friday' and FRI_BLOCK_START <= ts.start_time < FRI_BLOCK_END:
            blocked.add(i)
        if ts.start_time >= EVENING_CUTOFF:
            blocked.add(i)
    return blocked


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
        term_break_weeks = DEFAULT_TERM_BREAK_WEEKS

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


_DAY_NAMES = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')


def _build_adjacency_map(timeslots):
    """Map ts_idx -> list of ts_idx for same-day slots whose start_time == this slot's end_time.
    Used by soft constraints S1/S2 (mode-switch avoidance) to detect back-to-back placements.
    """
    start_lookup = defaultdict(list)   # (day, start_time) -> [ts_idx]
    for i, ts in enumerate(timeslots):
        start_lookup[(ts.day_of_week, ts.start_time)].append(i)

    adjacent_next = defaultdict(list)  # ts_idx -> [ts_idx, ...]
    for i, ts in enumerate(timeslots):
        for j in start_lookup.get((ts.day_of_week, ts.end_time), []):
            adjacent_next[i].append(j)
    return adjacent_next


def _mode_switch_penalties(model, entity_to_sessions, adjacent_next, compat_slots, get_slot_at, tag):
    """Soft constraints S1 (professor) / S2 (student group): penalise an Online<->F2F
    delivery-mode switch landing in two temporally-adjacent slots for the same entity.
    Returns a list of BoolVars (each = 1 iff that specific adjacency violation occurs).
    Fully reified both ways (viol==1 iff both slots are actually assigned) - a
    one-directional `OnlyEnforceIf(viol)` alone lets the solver always choose
    viol=0 for free since nothing then requires it to be 1 even when the real
    assignment satisfies the condition, which made this constraint silently
    inert (found 2026-07-12: a real generated schedule had 120 unpenalised
    mode-switches while this reported 0).
    """
    penalties = []
    for entity_id, sess_list in entity_to_sessions.items():
        # Pre-filter: skip entities with no genuine mode mix (cheap, avoids wasted work)
        modes = {s.delivery_mode for s in sess_list}
        if len(modes) < 2:
            continue
        n = len(sess_list)
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                si, sj = sess_list[i], sess_list[j]
                if si.delivery_mode == sj.delivery_mode:
                    continue
                if not _weeks_overlap(si, sj):
                    continue  # never actually adjacent in real calendar time
                # si immediately before sj
                for idx_i in compat_slots[si.id]:
                    for idx_j in adjacent_next.get(idx_i, ()):
                        if idx_j in compat_slots[sj.id]:
                            a, b = get_slot_at(si.id, idx_i), get_slot_at(sj.id, idx_j)
                            viol = model.NewBoolVar(f'{tag}_modesw_{si.id}_{idx_i}_{sj.id}_{idx_j}')
                            model.AddBoolAnd([a, b]).OnlyEnforceIf(viol)
                            model.AddBoolOr([a.Not(), b.Not()]).OnlyEnforceIf(viol.Not())
                            penalties.append(viol)
    return penalties


def _prof_idle_gap_penalties(model, by_prof, day_var_map, slot_vars, compat_slots, timeslots,
                              earliest_hour, latest_hour, threshold_hours=2):
    """Soft constraint S3: penalise a professor having > threshold_hours of idle time
    between their first and last class on a single day.
    Returns a list of BoolVars (each = 1 iff that professor's gap on that day exceeds the threshold).
    """
    sentinel_min = latest_hour + 1
    sentinel_max = earliest_hour - 1
    slot_start_hour = [ts.start_time.hour for ts in timeslots]
    slot_end_hour   = [ts.end_time.hour   for ts in timeslots]

    penalties = []
    for prof_id, sess_list in by_prof.items():
        if len(sess_list) < 2:
            continue
        for day_num, day_name in enumerate(_DAY_NAMES):
            relevant = [s for s in sess_list
                        if any(timeslots[i].day_of_week == day_name for i in compat_slots[s.id])]
            if len(relevant) < 2:
                continue  # can't have a gap with 0-1 sessions on this day

            starts, ends, on_day_bools = [], [], []
            for k, s in enumerate(relevant):
                on_day = model.NewBoolVar(f's3_onday_{prof_id}_{day_name}_{s.id}')
                model.Add(day_var_map[s.id] == day_num).OnlyEnforceIf(on_day)
                model.Add(day_var_map[s.id] != day_num).OnlyEnforceIf(on_day.Not())
                on_day_bools.append(on_day)

                raw_start = model.NewIntVar(0, 23, f's3_rawstart_{prof_id}_{day_name}_{s.id}')
                model.AddElement(slot_vars[s.id], slot_start_hour, raw_start)
                start_or_sentinel = model.NewIntVar(0, sentinel_min, f's3_start_{prof_id}_{day_name}_{s.id}')
                model.Add(start_or_sentinel == raw_start).OnlyEnforceIf(on_day)
                model.Add(start_or_sentinel == sentinel_min).OnlyEnforceIf(on_day.Not())
                starts.append(start_or_sentinel)

                raw_end = model.NewIntVar(0, 23, f's3_rawend_{prof_id}_{day_name}_{s.id}')
                model.AddElement(slot_vars[s.id], slot_end_hour, raw_end)
                end_or_sentinel = model.NewIntVar(sentinel_max, 23, f's3_end_{prof_id}_{day_name}_{s.id}')
                model.Add(end_or_sentinel == raw_end).OnlyEnforceIf(on_day)
                model.Add(end_or_sentinel == sentinel_max).OnlyEnforceIf(on_day.Not())
                ends.append(end_or_sentinel)

            any_two_on_day = model.NewBoolVar(f's3_any2_{prof_id}_{day_name}')
            model.Add(sum(on_day_bools) >= 2).OnlyEnforceIf(any_two_on_day)
            model.Add(sum(on_day_bools) <= 1).OnlyEnforceIf(any_two_on_day.Not())

            first_start = model.NewIntVar(0, sentinel_min, f's3_firststart_{prof_id}_{day_name}')
            model.AddMinEquality(first_start, starts)
            last_end = model.NewIntVar(sentinel_max, 23, f's3_lastend_{prof_id}_{day_name}')
            model.AddMaxEquality(last_end, ends)

            total_contact = model.NewIntVar(0, 23, f's3_contact_{prof_id}_{day_name}')
            model.Add(total_contact == sum(
                relevant[k].duration_hours * on_day_bools[k] for k in range(len(relevant))
            ))

            span = model.NewIntVar(-200, 30, f's3_span_{prof_id}_{day_name}')
            model.Add(span == last_end - first_start)

            idle = model.NewIntVar(-200, 30, f's3_idle_{prof_id}_{day_name}')
            model.Add(idle == span - total_contact)

            # Fully reified both ways so is_violated actually tracks idle >
            # threshold rather than the solver being free to always pick 0
            # (found 2026-07-12 - see _mode_switch_penalties for the same bug).
            is_violated = model.NewBoolVar(f's3_viol_{prof_id}_{day_name}')
            model.Add(idle > threshold_hours).OnlyEnforceIf([is_violated, any_two_on_day])
            model.Add(idle <= threshold_hours).OnlyEnforceIf([is_violated.Not(), any_two_on_day])
            model.Add(is_violated == 0).OnlyEnforceIf(any_two_on_day.Not())
            penalties.append(is_violated)
    return penalties


def _group_backtoback_penalties(model, grp_to_sessions, compat_slots, get_slot_at, timeslots,
                                 earliest_hour, latest_hour, limit_hours=2):
    """Soft constraint: penalise a student group having a zero-gap run of classes
    reaching limit_hours+1 hours or more in a single day. Overlapping sliding windows
    are intentionally not deduplicated - a longer unbroken run trips more windows,
    giving a naturally increasing penalty the longer the overrun.
    Returns a list of BoolVars (each = 1 iff that specific window is fully occupied).
    """
    w = limit_hours
    day_hour_ts = defaultdict(list)   # (day, hour) -> [ts_idx] covering that hour
    for i, ts in enumerate(timeslots):
        for h in range(ts.start_time.hour, ts.end_time.hour):
            day_hour_ts[(ts.day_of_week, h)].append(i)

    penalties = []
    for grp_id, grp_sess in grp_to_sessions.items():
        for day in _DAY_NAMES:
            occupied = {}
            for h in range(earliest_hour, latest_hour):
                ts_indices = day_hour_ts.get((day, h), [])
                if not ts_indices:
                    continue
                rel = [s for s in grp_sess if any(idx in compat_slots[s.id] for idx in ts_indices)]
                if not rel:
                    continue
                occ = model.NewBoolVar(f'btb_occ_{grp_id}_{day}_{h}')
                indicators = [get_slot_at(s.id, idx) for s in rel for idx in ts_indices
                              if idx in compat_slots[s.id]]
                model.AddBoolOr(indicators).OnlyEnforceIf(occ)
                model.AddBoolAnd([v.Not() for v in indicators]).OnlyEnforceIf(occ.Not())
                occupied[h] = occ

            for window_start in range(earliest_hour, latest_hour - w):
                window_hours = [h for h in range(window_start, window_start + w + 1) if h in occupied]
                if len(window_hours) < w + 1:
                    continue  # window not fully reachable - skip
                window_sum = sum(occupied[h] for h in window_hours)
                violated = model.NewBoolVar(f'btb_viol_{grp_id}_{day}_{window_start}')
                # Fully reified both ways (same fix as the mode-switch/idle-gap
                # bug, 2026-07-12) so `violated` actually tracks window_sum > w.
                model.Add(window_sum > w).OnlyEnforceIf(violated)
                model.Add(window_sum <= w).OnlyEnforceIf(violated.Not())
                penalties.append(violated)
    return penalties


def _room_util_indicator_array(rooms, group_size, threshold=0.6):
    """Flat array over all rooms: 1 if placing a class of group_size there would leave
    the room under `threshold` utilisation, else 0. Used with AddElement (S6)."""
    return [1 if (group_size / r.capacity) < threshold else 0 for r in rooms]


# ---------------------------------------------------------------------------
# Main solver
# ---------------------------------------------------------------------------

def solve(trimester, start_date, term_break_weeks=None, trimester_num=None, academic_year=None,
          pinned_slots=None, historical_preferred=None):
    """
    Run CP-SAT to schedule all ready ClassSessions for the given trimester.

    Args:
        trimester         : str            - internal key, e.g. 'AY2526-T1'
        start_date        : date           - Monday of Week 1
        term_break_weeks  : set[int]       - week numbers to mark as term breaks (default: {7})
        trimester_num     : int|None       - 1, 2, or 3 - filters sessions by ClassSession.trimester
        academic_year     : str|None       - e.g. 'AY2526' - tagged onto each TimetableEntry
        pinned_slots         : dict[int, int]          - {session_id: timeslot_id} to preserve from last run
                                                         (Option A partial re-generation - ignored if strictly blocked)
        historical_preferred : dict[int, int]           - {class_session_id: timeslot_id}
                                                         soft preference to follow previous year's slot pattern

    Returns:
        (success: bool, message: str, stats: dict)
    """
    if term_break_weeks is None:
        term_break_weeks = DEFAULT_TERM_BREAK_WEEKS

    # 1. Load sessions that are ready:
    #    - f2f sessions need a student group (room/time can still be assigned
    #      without a professor - a class missing its lecturer's name still
    #      needs to happen; the gap is disclosed on System Info instead of
    #      silently dropping the class from the timetable)
    #    - online sessions need neither a room nor a group
    from sqlalchemy import or_
    filters = [
        or_(
            ClassSession.delivery_mode == 'online',
            ClassSession.student_group_id.isnot(None),
        ),
    ]
    # Filter by trimester number if specified (1, 2, or 3)
    if trimester_num is not None:
        filters.append(ClassSession.trimester == trimester_num)

    all_sessions = ClassSession.query.filter(*filters).all()

    # Async sessions: no timeslot needed - excluded from solver, no TimetableEntry created
    async_sessions = [s for s in all_sessions if s.is_async]
    sessions = [s for s in all_sessions if not s.is_async]

    if not sessions:
        return False, 'No sessions are ready to schedule. Assign professors and student groups first.', {}

    timeslots = (TimeSlot.query
                 .order_by(TimeSlot.day_of_week, TimeSlot.period_label)
                 .all())
    rooms = Room.query.filter_by(is_active=True).order_by(Room.room_code).all()
    room_id_to_index = {r.id: i for i, r in enumerate(rooms)}

    # Build a fast lookup: timeslot.id → index in `timeslots` list
    ts_id_to_index = {ts.id: i for i, ts in enumerate(timeslots)}

    # Operating-hour bounds derived from actual timeslot data - used by S3 (idle gap)
    # and the group back-to-back constraint below.
    EARLIEST_HOUR = min(ts.start_time.hour for ts in timeslots)
    LATEST_HOUR   = max(ts.end_time.hour   for ts in timeslots)

    # 2. Build compatibility maps
    # Institutional blocked slots (applied to all sessions without a fixed pin)
    inst_blocked = _institutional_blocked_indices(timeslots)

    # Calendar / public-holiday / cancelled-event lookups, computed up front
    # (not just at entry-writing time) so the domain-building step below can
    # steer sessions AWAY from a day that would silently wipe out every one
    # of their occurrences. Found 2026-07-16: a session's day/time used to be
    # chosen with zero awareness of PH/events - fine for a normal session
    # with many teaching weeks (losing 1 week to a holiday is a minor, VISIBLE
    # gap), but a session with only 1-3 sparse teaching weeks (a one-off quiz,
    # for example) could have its single occurrence land on a blocked date and
    # vanish entirely - reported as "scheduled" by the solver's own stats,
    # invisible in every export including Template 2.
    cal_weeks = _get_or_create_calendar(trimester, start_date, term_break_weeks)
    non_break_weeks = [w for w in cal_weeks if not w.is_term_break]
    if non_break_weeks:
        ph_dates = _get_sg_holidays(non_break_weeks[0].start_date, non_break_weeks[-1].end_date)
    else:
        ph_dates = set()
    from app.models.event import Event
    event_filters = [Event.outcome == 'cancel']
    if trimester_num is not None:
        event_filters.append(db.or_(Event.trimester == None, Event.trimester == trimester_num))
    if academic_year is not None:
        event_filters.append(db.or_(Event.academic_year == None, Event.academic_year == academic_year))
    cancel_events = Event.query.filter(*event_filters).all()
    event_blocks = {}  # date -> set of blocked timeslot_ids (empty set = full day)
    for ev in cancel_events:
        if ev.event_date not in event_blocks:
            event_blocks[ev.event_date] = set() if ev.is_full_day else set(ev.blocked_timeslot_ids)
        else:
            if ev.is_full_day:
                event_blocks[ev.event_date] = set()
            elif event_blocks[ev.event_date]:
                event_blocks[ev.event_date].update(ev.blocked_timeslot_ids)
    _day_offset = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4}

    def _session_teaching_weeks_outside_calendar(session_obj):
        """True if session_obj.teaching_weeks is set but none of those week
        numbers exist in this trimester's actual (non-break) calendar - e.g.
        a session pointing at week 14 when the trimester only runs 13 weeks.
        Day-independent (unlike PH/event blocking), so checked once per
        session rather than per candidate timeslot."""
        if not session_obj.teaching_weeks:
            return False
        try:
            allowed_weeks = {int(w) for w in session_obj.teaching_weeks.split(',') if w.strip()}
        except ValueError:
            return False
        return not any(w.week_number in allowed_weeks for w in non_break_weeks)

    def _all_occurrences_blocked(session_obj, ts):
        """True if EVERY one of session_obj's teaching weeks lands on a public
        holiday or cancelled event for timeslot ts - i.e. picking ts would
        produce zero real TimetableEntry rows for this session."""
        if not session_obj.teaching_weeks:
            return False
        try:
            allowed_weeks = {int(w) for w in session_obj.teaching_weeks.split(',') if w.strip()}
        except ValueError:
            return False
        relevant_weeks = [w for w in non_break_weeks if w.week_number in allowed_weeks]
        if not relevant_weeks:
            return False  # handled separately by _session_teaching_weeks_outside_calendar
        day_offset = _day_offset.get(ts.day_of_week, 0)
        for week in relevant_weeks:
            d = week.start_date + timedelta(days=day_offset)
            if d in ph_dates:
                continue
            blocked_ts = event_blocks.get(d)
            if blocked_ts is not None and (not blocked_ts or ts.id in blocked_ts):
                continue
            return False  # at least one real occurrence survives on this day
        return True

    compat_slots = {}   # session.id → [timeslot indices]
    no_timeslot_warnings = []   # sessions with genuinely zero matching time slots
    for s in sessions:
        compat = [i for i, ts in enumerate(timeslots) if _slot_compatible(ts, s)]
        # Remove institutionally blocked slots unless the session has a fixed pin there
        if not s.fixed_timeslot_id:
            compat = [i for i in compat if i not in inst_blocked]
        if not compat:
            # A session with zero matching time slots is a DATA gap (nothing
            # of the right length/day exists yet), not a scheduling conflict -
            # skip it and keep going rather than fail the whole trimester, the
            # same way a no-compatible-room session is already handled below
            # (found 2026-07-11: one such session used to abort 500+ others).
            no_timeslot_warnings.append(
                f'{s.course.module_code} ({s.session_type}, {s.duration_hours}h) - '
                f'no time slot of this length exists in the system - skipped.'
            )
            continue
        if _session_teaching_weeks_outside_calendar(s):
            # Found 2026-07-16: some quiz sessions reference a teaching week
            # (e.g. 14 or 15) that doesn't exist in this trimester's actual
            # calendar (e.g. only 13 weeks) - can never produce a real entry
            # no matter which day/room is picked, so this must be caught here
            # rather than left to silently vanish after solving.
            no_timeslot_warnings.append(
                f'{s.course.module_code} ({s.session_type}) - teaching week(s) {s.teaching_weeks} '
                f"fall outside this trimester's {len(non_break_weeks)}-week calendar - skipped."
            )
            continue
        compat_after_calendar = [i for i in compat if not _all_occurrences_blocked(s, timeslots[i])]
        if not compat_after_calendar:
            no_timeslot_warnings.append(
                f'{s.course.module_code} ({s.session_type}) - every candidate day/time falls on a '
                f"public holiday or cancelled event for this session's teaching week(s) "
                f'({s.teaching_weeks}) - skipped rather than silently producing zero sessions.'
            )
            continue
        compat_slots[s.id] = compat_after_calendar

    if no_timeslot_warnings:
        sessions = [s for s in sessions if s.id in compat_slots]

    if not sessions:
        return False, (
            'No sessions could be scheduled - none have a compatible time slot. '
            'Check the Timeslots page.'
        ), {}

    # Combined enrollment for sessions linked via a SharedModuleGroup (Common
    # Modules / Programme Grouping) - room must fit ALL linked programmes'
    # students together, not just this one session's own group.
    shared_group_combined_size = {}   # shared_module_group_id -> combined intake_size
    for s in sessions:
        if s.shared_module_group_id and s.student_group:
            shared_group_combined_size[s.shared_module_group_id] = (
                shared_group_combined_size.get(s.shared_module_group_id, 0)
                + s.student_group.intake_size
            )

    compat_rooms = {}           # session.id → [room indices]  (f2f only)
    no_room_warnings = []       # sessions skipped due to room capacity
    for s in sessions:
        if s.delivery_mode == 'f2f':
            size_override = shared_group_combined_size.get(s.shared_module_group_id)
            compat = [i for i, r in enumerate(rooms) if _room_compatible(r, s, size_override)]
            # A pinned room (fixed_room_id) must be in this session's domain
            # even if it fails the capacity check - see _room_compatible's
            # require_capacity docstring for why capacity is skipped here.
            if s.fixed_room_id and s.fixed_room_id in room_id_to_index:
                pinned_idx = room_id_to_index[s.fixed_room_id]
                if pinned_idx not in compat and _room_compatible(rooms[pinned_idx], s, require_capacity=False):
                    compat.append(pinned_idx)
            if not compat:
                group_size = size_override if size_override is not None else (
                    s.student_group.intake_size if s.student_group else '?'
                )
                no_room_warnings.append(
                    f'{s.course.module_code} ({s.session_type}, group size {group_size}): '
                    f'no compatible room - skipped.'
                )
            else:
                compat_rooms[s.id] = compat

    # Drop sessions with no compatible room (can't be scheduled - skip gracefully)
    if no_room_warnings:
        skip_ids = {s.id for s in sessions if s.delivery_mode == 'f2f' and s.id not in compat_rooms}
        for sid in skip_ids:
            compat_slots.pop(sid, None)
        sessions = [s for s in sessions if s.id not in skip_ids]

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
    # the problem is immediately infeasible - catch it early with a clear message.
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

    # Live soft-constraint weights (admin overrides from Admin Tools >
    # Constraint Settings, falling back to solver.py's own defaults). A
    # weight of 0 means the constraint is disabled for this run.
    W = get_effective_soft_weights()

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

    # Hard constraint - shared module groups (Common Modules / Programme Grouping).
    # Sessions linked via shared_module_group_id must land in the SAME slot and
    # (if f2f) the SAME room - they represent one combined class across programmes.
    shared_group_sessions = defaultdict(list)
    for s in sessions:
        if s.shared_module_group_id:
            shared_group_sessions[s.shared_module_group_id].append(s)
    for _grp_id, sess_list in shared_group_sessions.items():
        for i in range(1, len(sess_list)):
            model.Add(slot_vars[sess_list[i].id] == slot_vars[sess_list[0].id])
            if sess_list[i].id in room_vars and sess_list[0].id in room_vars:
                model.Add(room_vars[sess_list[i].id] == room_vars[sess_list[0].id])

    # Hard constraint A - no professor double-booking (all professors incl. co-teachers)
    # Use pairwise != instead of AddAllDifferent so we can skip parallel-fixed pairs and
    # sessions whose teaching_weeks don't overlap (they're sequential, same slot is fine).
    by_prof = defaultdict(list)
    for s in sessions:
        for prof_id in s.all_professor_ids:
            by_prof[prof_id].append(s)
    for sess_list in by_prof.values():
        for i in range(len(sess_list)):
            for j in range(i + 1, len(sess_list)):
                si, sj = sess_list[i], sess_list[j]
                if (si.fixed_timeslot_id is not None
                        and si.fixed_timeslot_id == sj.fixed_timeslot_id):
                    continue  # parallel sections pinned to same slot
                if (si.shared_module_group_id is not None
                        and si.shared_module_group_id == sj.shared_module_group_id):
                    continue  # deliberately co-taught as one combined class
                if not _weeks_overlap(si, sj):
                    continue  # non-overlapping week ranges - same slot is fine
                model.Add(slot_vars[si.id] != slot_vars[sj.id])

    # Hard constraint B - no student-group double-booking (parent/child aware)
    # Only enforced when both sessions share at least one teaching week.
    for i in range(len(sessions)):
        for j in range(i + 1, len(sessions)):
            si, sj = sessions[i], sessions[j]
            if si.student_group_id and sj.student_group_id:
                if sj.student_group_id in _conflicting_group_ids(si.student_group_id):
                    if (si.fixed_timeslot_id is not None
                            and si.fixed_timeslot_id == sj.fixed_timeslot_id):
                        continue  # parallel sections
                    if not _weeks_overlap(si, sj):
                        continue  # sequential week ranges - same slot is fine
                    model.Add(slot_vars[si.id] != slot_vars[sj.id])

    # Hard constraint C - no room double-booking
    # Only enforced when both sessions share at least one teaching week (sequential sessions
    # in the same room at the same timeslot don't actually clash).
    f2f = [s for s in sessions if s.delivery_mode == 'f2f']
    for i in range(len(f2f)):
        for j in range(i + 1, len(f2f)):
            si, sj = f2f[i], f2f[j]
            if not _weeks_overlap(si, sj):
                continue  # sequential week ranges can reuse same room + slot
            if (si.shared_module_group_id is not None
                    and si.shared_module_group_id == sj.shared_module_group_id):
                continue  # deliberately sharing the same slot AND room (combined class)
            same = model.NewBoolVar(f'same_{si.id}_{sj.id}')
            model.Add(slot_vars[si.id] == slot_vars[sj.id]).OnlyEnforceIf(same)
            model.Add(slot_vars[si.id] != slot_vars[sj.id]).OnlyEnforceIf(same.Not())
            model.Add(room_vars[si.id] != room_vars[sj.id]).OnlyEnforceIf(same)

    # Hard constraint D - fixed timeslot pins
    # Priority: fixed_timeslot_id (admin-set permanent pin) > pinned_slots (Option A carry-over)
    # Safety: never pin to a slot that is strictly blocked for any professor - that would make
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

    # Hard constraint D2 - fixed room pins (from cleaned-data venue codes)
    # Same silently-drop-if-infeasible safety as the timeslot pins above: a
    # named venue that turns out to be the wrong type/too small for this
    # session's group would make the model infeasible, so skip enforcing it
    # rather than block generation entirely.
    room_pins_applied = 0
    room_pins_dropped = 0
    for s in sessions:
        if not s.fixed_room_id or s.delivery_mode != 'f2f':
            continue
        room_idx = room_id_to_index.get(s.fixed_room_id)
        if room_idx is None or room_idx not in compat_rooms.get(s.id, []):
            room_pins_dropped += 1
            continue
        model.Add(room_vars[s.id] == room_idx)
        room_pins_applied += 1

    # Hard constraint E - strict availability declarations (all professors)
    strict_constraints_added = 0
    for s in sessions:
        blocked_indices = set()
        for prof_id in s.all_professor_ids:
            blocked_indices |= strict_blocked.get(prof_id, set())
        for blocked_idx in blocked_indices:
            if blocked_idx in compat_slots[s.id]:
                model.Add(slot_vars[s.id] != blocked_idx)
                strict_constraints_added += 1

    # Shared BoolVar cache: (session_id, ts_idx) -> "session placed at that slot".
    # Used by the lunch-window hard constraint below and by soft constraints
    # S1/S2 (mode-switch) and the group back-to-back constraint.
    _slot_at_vars = {}

    def _get_slot_at(session_id, ts_idx):
        key = (session_id, ts_idx)
        if key not in _slot_at_vars:
            b = model.NewBoolVar(f'lat_{session_id}_{ts_idx}')
            model.Add(slot_vars[session_id] == ts_idx).OnlyEnforceIf(b)
            model.Add(slot_vars[session_id] != ts_idx).OnlyEnforceIf(b.Not())
            _slot_at_vars[key] = b
        return _slot_at_vars[key]

    # Hard constraint F - lunch window
    # Every student group must have ≥ 1 free hour in [LUNCH_WINDOW_START, LUNCH_WINDOW_END)
    # on each day. Not a fixed block - any one of the hourly chunks may be free.
    LUNCH_HOURS = list(range(LUNCH_WINDOW_START.hour, LUNCH_WINDOW_END.hour))

    # (day, chunk_hour) → list of timeslot indices whose time range covers that hour
    day_chunk_ts = defaultdict(list)
    for i, ts in enumerate(timeslots):
        for h in LUNCH_HOURS:
            if ts.start_time.hour <= h < ts.end_time.hour:
                day_chunk_ts[(ts.day_of_week, h)].append(i)

    grp_to_sessions = defaultdict(list)
    for s in sessions:
        if s.student_group_id:
            grp_to_sessions[s.student_group_id].append(s)

    for grp_id, grp_sess in grp_to_sessions.items():
        for day in ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'):
            chunk_occ_vars = []
            for h in LUNCH_HOURS:
                ts_indices = day_chunk_ts.get((day, h), [])
                if not ts_indices:
                    continue
                # sessions in this group that can land on any slot covering (day, h)
                rel = [s for s in grp_sess
                       if any(idx in compat_slots[s.id] for idx in ts_indices)]
                if not rel:
                    continue
                occ = model.NewBoolVar(f'lunch_{grp_id}_{day}_{h}')
                indicators = [_get_slot_at(s.id, idx) for s in rel for idx in ts_indices
                              if idx in compat_slots[s.id]]
                if not indicators:
                    continue
                model.AddBoolOr(indicators).OnlyEnforceIf(occ)
                model.AddBoolAnd([v.Not() for v in indicators]).OnlyEnforceIf(occ.Not())
                chunk_occ_vars.append(occ)
            # Only add constraint when every chunk in the window is reachable -
            # otherwise trivially OK. Require >=1 free hour among the chunks.
            if len(chunk_occ_vars) == len(LUNCH_HOURS):
                model.Add(sum(chunk_occ_vars) <= len(LUNCH_HOURS) - 1)

    # Soft constraint G - preferred availability declarations
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

    # Soft constraint H - day clustering per student group
    # Minimise the number of campus days per group by penalising session pairs
    # from the same group that fall on DIFFERENT days.
    # Weight 1 - tiebreaker only, never overrides hard or stronger soft constraints.
    _day_num    = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4}
    slot_to_day = [_day_num.get(ts.day_of_week, 5) for ts in timeslots]

    day_var_map = {}
    for s in sessions:
        dv = model.NewIntVar(0, 5, f'day_{s.id}')
        model.AddElement(slot_vars[s.id], slot_to_day, dv)
        day_var_map[s.id] = dv

    # Hard constraint - university-wide module day separation.
    # UCS1001/UDE2222 each require their weekly sessions to land on different
    # days ("at least 1 day apart") for the same cohort. Scoped to these two
    # module codes only - the only ones named with this explicit pattern.
    uniwide_by_group = defaultdict(list)  # (module_code, student_group_id) -> [sessions]
    for s in sessions:
        code = s.course.module_code
        if code in UNIWIDE_DAY_SEPARATED_MODULES and s.student_group_id:
            uniwide_by_group[(code, s.student_group_id)].append(s)
    for (_code, _grp), sess_list in uniwide_by_group.items():
        for i in range(len(sess_list)):
            for j in range(i + 1, len(sess_list)):
                model.Add(day_var_map[sess_list[i].id] != day_var_map[sess_list[j].id])

    cluster_cost_vars = []
    for i in range(len(sessions)):
        for j in range(i + 1, len(sessions)):
            si, sj = sessions[i], sessions[j]
            # Only cluster within the same student group
            if (not si.student_group_id or not sj.student_group_id
                    or si.student_group_id != sj.student_group_id):
                continue
            diff_day_b = model.NewBoolVar(f'diff_{si.id}_{sj.id}')
            model.Add(day_var_map[si.id] != day_var_map[sj.id]).OnlyEnforceIf(diff_day_b)
            model.Add(day_var_map[si.id] == day_var_map[sj.id]).OnlyEnforceIf(diff_day_b.Not())
            cluster_cost_vars.append(diff_day_b)

    # Soft constraint I (new) - preferred timeslot from Remarks auto-parse
    # When ClassSession.preferred_timeslot_id is set (from Remarks column), prefer that slot.
    # Weight 50 - stronger than historical (30) but weaker than strict availability (100).
    pref_ts_cost_vars = []
    for s in sessions:
        if not s.preferred_timeslot_id:
            continue
        pref_idx = ts_id_to_index.get(s.preferred_timeslot_id)
        if pref_idx is None or pref_idx not in compat_slots[s.id]:
            continue
        not_pref = model.NewBoolVar(f'not_pref_ts_{s.id}')
        model.Add(slot_vars[s.id] != pref_idx).OnlyEnforceIf(not_pref)
        model.Add(slot_vars[s.id] == pref_idx).OnlyEnforceIf(not_pref.Not())
        pref_ts_cost_vars.append(not_pref)

    # Soft constraint J - lecture before tutorial / lab (same course)
    # If a course has both a lecture/lectorial and a tutorial and/or lab, prefer the
    # lecture to fall on an earlier (or same) weekday than each. Weight 5 each.
    lec_tut_cost_vars = []
    lec_lab_cost_vars = []
    from collections import defaultdict as _dd
    course_type_map = _dd(dict)  # course_id → {session_type: session}
    for s in sessions:
        st = s.session_type
        if st in ('lecture', 'lectorial'):
            course_type_map[s.course_id]['lecture'] = s
        elif st == 'tutorial':
            course_type_map[s.course_id]['tutorial'] = s
        elif st == 'lab':
            course_type_map[s.course_id]['lab'] = s

    for course_id, tmap in course_type_map.items():
        lec = tmap.get('lecture')
        tut = tmap.get('tutorial')
        lab = tmap.get('lab')
        if lec and tut:
            wrong_order = model.NewBoolVar(f'lec_after_tut_{course_id}')
            model.Add(day_var_map[lec.id] > day_var_map[tut.id]).OnlyEnforceIf(wrong_order)
            model.Add(day_var_map[lec.id] <= day_var_map[tut.id]).OnlyEnforceIf(wrong_order.Not())
            lec_tut_cost_vars.append(wrong_order)
        if lec and lab:
            wrong_order_lab = model.NewBoolVar(f'lec_after_lab_{course_id}')
            model.Add(day_var_map[lec.id] > day_var_map[lab.id]).OnlyEnforceIf(wrong_order_lab)
            model.Add(day_var_map[lec.id] <= day_var_map[lab.id]).OnlyEnforceIf(wrong_order_lab.Not())
            lec_lab_cost_vars.append(wrong_order_lab)

    # Soft constraint I - historical slot preference
    # When generating a new AY, prefer the same timeslot that was used in the previous year's
    # equivalent trimester (e.g. AY2627-T1 prefers AY2526-T1 slots).
    # Weight = 10: stronger than day-spread (1) but weaker than availability declarations (100).
    hist_penalty_vars = []   # BoolVar: 1 = session NOT in historical slot
    if historical_preferred:
        for s in sessions:
            hist_ts_id = historical_preferred.get(s.id)
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
            # Hint (not a constraint) - start the search assuming this slot,
            # so CP-SAT converges on reproducing the historical/backbone
            # schedule instead of just any feasible solution within the time
            # budget. Large combined solves (500+ sessions) were otherwise
            # returning "Feasible" (not "Optimal") with a low match rate even
            # when the preferred slot had no real conflict (found 2026-07-10).
            model.AddHint(slot_vars[s.id], hist_idx)

    # Soft constraint K - prefer sessions end by 17:00 (S9)
    # Weight 15: between lec-order (5) and historical (30)
    late_end_cost_vars = []
    _late_map = [1 if ts.end_time > LATE_END_CUTOFF else 0 for ts in timeslots]
    if any(_late_map):
        for s in sessions:
            s_slots = list(compat_slots[s.id])
            if not any(_late_map[i] for i in s_slots):
                continue
            if all(_late_map[i] for i in s_slots):
                continue  # all compatible slots are late - penalty useless
            is_late = model.NewIntVar(0, 1, f'is_late_{s.id}')
            model.AddElement(slot_vars[s.id], _late_map, is_late)
            late_end_cost_vars.append(is_late)

    # Soft constraint L - avoid scheduling student groups in first or last slot of day (S7)
    # Weight 2: tiebreaker
    _day_starts = defaultdict(list)
    for i, ts in enumerate(timeslots):
        _day_starts[ts.day_of_week].append((ts.start_time, i))
    _extremal_indices = set()
    for sl in _day_starts.values():
        if not sl:
            continue
        sl.sort()
        _extremal_indices.add(sl[0][1])
        _extremal_indices.add(sl[-1][1])
    extremal_cost_vars = []
    _extr_map = [1 if i in _extremal_indices else 0 for i in range(len(timeslots))]
    if any(_extr_map):
        for s in sessions:
            if not s.student_group_id:
                continue
            s_slots = list(compat_slots[s.id])
            if not any(_extr_map[i] for i in s_slots):
                continue
            if all(_extr_map[i] for i in s_slots):
                continue
            is_extr = model.NewIntVar(0, 1, f'extr_{s.id}')
            model.AddElement(slot_vars[s.id], _extr_map, is_extr)
            extremal_cost_vars.append(is_extr)

    # Soft constraint M - mode-switch avoidance, professor (S1)
    # Weight W['S1']: penalise adjacent-slot Online<->F2F transitions for the
    # same professor on the same day. Skipped entirely (not just zero-weighted)
    # when disabled - these are among the most expensive constraint blocks to
    # build (pairwise per entity), so an admin turning one off should also
    # get the solve-time benefit, not just a neutral objective term.
    adjacent_next = _build_adjacency_map(timeslots)
    mode_switch_prof_vars = _mode_switch_penalties(
        model, by_prof, adjacent_next, compat_slots, _get_slot_at, tag='prof'
    ) if W['S1'] > 0 else []

    # Soft constraint N - mode-switch avoidance, student group (S2)
    # Weight W['S2']: same as above, keyed by student group.
    mode_switch_grp_vars = _mode_switch_penalties(
        model, grp_to_sessions, adjacent_next, compat_slots, _get_slot_at, tag='grp'
    ) if W['S2'] > 0 else []

    # Soft constraint O - professor idle-gap avoidance (S3)
    # Weight W['S3']: penalise > PROF_IDLE_GAP_THRESHOLD_HOURS of idle time
    # between a professor's first and last class on a given day.
    prof_idle_gap_vars = _prof_idle_gap_penalties(
        model, by_prof, day_var_map, slot_vars, compat_slots, timeslots,
        EARLIEST_HOUR, LATEST_HOUR, threshold_hours=PROF_IDLE_GAP_THRESHOLD_HOURS,
    ) if W['S3'] > 0 else []

    # Soft constraint P - group back-to-back stacking avoidance (S8)
    # Weight W['S8']: penalise a student group having a zero-gap run of
    # classes reaching GROUP_BACKTOBACK_LIMIT_HOURS+1 hours or more in a day.
    group_backtoback_vars = _group_backtoback_penalties(
        model, grp_to_sessions, compat_slots, _get_slot_at, timeslots,
        EARLIEST_HOUR, LATEST_HOUR, limit_hours=GROUP_BACKTOBACK_LIMIT_HOURS,
    ) if W['S8'] > 0 else []

    # Soft constraint Q - room utilisation (S6)
    # Weight WEIGHT_ROOM_UTIL: penalise f2f sessions placed in a room where
    # group_size / room.capacity < ROOM_UTIL_THRESHOLD (under-utilised room).
    room_util_cost_vars = []
    for s in sessions:
        if s.delivery_mode != 'f2f' or s.id not in room_vars:
            continue
        group_size = s.student_group.intake_size if s.student_group else 1
        util_array = _room_util_indicator_array(rooms, group_size, threshold=ROOM_UTIL_THRESHOLD)
        if not any(util_array[r] for r in compat_rooms[s.id]):
            continue
        if all(util_array[r] for r in compat_rooms[s.id]):
            continue
        low_util = model.NewIntVar(0, 1, f's6_lowutil_{s.id}')
        model.AddElement(room_vars[s.id], util_array, low_util)
        room_util_cost_vars.append(low_util)

    # Soft constraint R - room best-fit (prefer the tightest-fitting compatible room,
    # not just any room that's big enough). Wasted-seat count is capped at
    # ROOM_BEST_FIT_CAP so one large mismatch can't swamp the objective relative
    # to other tiebreaker-tier terms - this is a preference, not a hard override.
    ROOM_BEST_FIT_CAP = 5
    room_best_fit_cost_vars = []
    for s in sessions:
        if s.delivery_mode != 'f2f' or s.id not in room_vars:
            continue
        group_size = s.student_group.intake_size if s.student_group else 1
        wasted_array = [min(max(0, r.capacity - group_size), ROOM_BEST_FIT_CAP) for r in rooms]
        if not any(wasted_array[r] for r in compat_rooms[s.id]):
            continue  # every compatible room is already a perfect/near-perfect fit
        wasted = model.NewIntVar(0, ROOM_BEST_FIT_CAP, f's_bestfit_{s.id}')
        model.AddElement(room_vars[s.id], wasted_array, wasted)
        room_best_fit_cost_vars.append(wasted)

    # Soft constraint S - consistent venue for adjacent-slot classes (same
    # professor or same group). Weight WEIGHT_CONSISTENT_VENUE: reuses the same
    # adjacency map as the mode-switch constraints.
    def _consistent_venue_penalties(entity_to_sessions, tag):
        # both_here and viol are fully reified both ways (same fix as the
        # mode-switch/idle-gap/back-to-back bug, 2026-07-12) - a one-directional
        # OnlyEnforceIf(viol) alone left the solver free to always pick 0,
        # making this constraint silently inert regardless of the real layout.
        penalties = []
        for entity_id, sess_list in entity_to_sessions.items():
            f2f_sess = [s for s in sess_list if s.delivery_mode == 'f2f' and s.id in room_vars]
            n = len(f2f_sess)
            for i in range(n):
                for j in range(n):
                    if i == j:
                        continue
                    si, sj = f2f_sess[i], f2f_sess[j]
                    if not _weeks_overlap(si, sj):
                        continue  # never actually adjacent in real calendar time
                    for idx_i in compat_slots[si.id]:
                        for idx_j in adjacent_next.get(idx_i, ()):
                            if idx_j not in compat_slots[sj.id]:
                                continue
                            a, b = _get_slot_at(si.id, idx_i), _get_slot_at(sj.id, idx_j)
                            both_here = model.NewBoolVar(f'{tag}_venueadj_{si.id}_{idx_i}_{sj.id}_{idx_j}')
                            model.AddBoolAnd([a, b]).OnlyEnforceIf(both_here)
                            model.AddBoolOr([a.Not(), b.Not()]).OnlyEnforceIf(both_here.Not())
                            diff_room = model.NewBoolVar(f'{tag}_diffroom_{si.id}_{sj.id}_{idx_i}_{idx_j}')
                            model.Add(room_vars[si.id] != room_vars[sj.id]).OnlyEnforceIf(diff_room)
                            model.Add(room_vars[si.id] == room_vars[sj.id]).OnlyEnforceIf(diff_room.Not())
                            viol = model.NewBoolVar(f'{tag}_venueviol_{si.id}_{idx_i}_{sj.id}_{idx_j}')
                            model.AddBoolAnd([both_here, diff_room]).OnlyEnforceIf(viol)
                            model.AddBoolOr([both_here.Not(), diff_room.Not()]).OnlyEnforceIf(viol.Not())
                            penalties.append(viol)
        return penalties

    if W['S11'] > 0:
        consistent_venue_prof_vars = _consistent_venue_penalties(by_prof, 'profvenue')
        consistent_venue_grp_vars  = _consistent_venue_penalties(grp_to_sessions, 'grpvenue')
    else:
        consistent_venue_prof_vars = []
        consistent_venue_grp_vars  = []

    # Combined objective (minimise):
    #   availability violations  → weight 100  (highest - never violate if possible)
    #   preferred timeslot miss  → weight 50   (Remarks auto-parsed requests)
    #   historical slot changes  → weight 30   (continuity across AYs)
    #   end-after-17:00          → weight 15   (prefer sessions finish by 17:00)
    #   mode-switch (prof/group) → weight 8    (Online<->F2F back-to-back)
    #   idle-gap / back-to-back  → weight 6    (professor idle time / group stacking)
    #   lec-before-tut/lab violation → weight 5    (pedagogical ordering)
    #   room utilisation         → weight 3    (avoid under-filled rooms)
    #   room best-fit / consistent venue / first-last slot → weight 2 (tiebreakers)
    #   day clustering miss      → weight 1    (tiebreaker: fewer campus days per group)
    obj_terms = []
    for pv, *_ in penalty_vars:
        obj_terms.append(W['S-avail'] * pv)
    for not_pref in pref_ts_cost_vars:
        obj_terms.append(W['S-pref-ts'] * not_pref)
    for not_hist, *_ in hist_penalty_vars:
        obj_terms.append(W['S-hist'] * not_hist)
    for iv in late_end_cost_vars:
        obj_terms.append(W['S9'] * iv)
    for v in mode_switch_prof_vars:
        obj_terms.append(W['S1'] * v)
    for v in mode_switch_grp_vars:
        obj_terms.append(W['S2'] * v)
    for v in prof_idle_gap_vars:
        obj_terms.append(W['S3'] * v)
    for v in group_backtoback_vars:
        obj_terms.append(W['S8'] * v)
    for wrong in lec_tut_cost_vars:
        obj_terms.append(W['S-lec-tut'] * wrong)
    for wrong in lec_lab_cost_vars:
        obj_terms.append(W['S-lec-lab'] * wrong)
    for v in room_util_cost_vars:
        obj_terms.append(W['S6'] * v)
    for wv in room_best_fit_cost_vars:
        obj_terms.append(W['S10'] * wv)
    for v in consistent_venue_prof_vars:
        obj_terms.append(W['S11'] * v)
    for v in consistent_venue_grp_vars:
        obj_terms.append(W['S11'] * v)
    for iv in extremal_cost_vars:
        obj_terms.append(W['S7'] * iv)
    for dv in cluster_cost_vars:
        obj_terms.append(W['S5'] * dv)

    if obj_terms:
        model.Minimize(sum(obj_terms))

    # 5. Solve
    cp_solver = cp_model.CpSolver()
    # Raised from 180 to 400 on 2026-07-16 - T1 and T2 were both hitting the
    # old cap and returning "Feasible" (not proven optimal), meaning genuine
    # search room was being left on the table, not a hard capacity ceiling.
    # Tested directly: 180s -> 400s dropped T1's soft violations 698 -> 500
    # (score 47 -> 58) and T2's 47 -> fewer as well; T3 was already Optimal
    # and unaffected either way, so raising this costs nothing for smaller
    # trimesters (they still finish as soon as they hit the gap-limit below).
    cp_solver.parameters.max_time_in_seconds = 400
    cp_solver.parameters.num_search_workers  = 8
    # Accept a solution within 5% of provably optimal rather than always chasing the
    # full time budget - the added soft-constraint complexity makes "optimal" and
    # "very good" practically indistinguishable, this just returns sooner. Loosened
    # from 2% on 2026-07-12 after fully reifying S1/S2/S3/S8/S11 (see the fix notes
    # on those functions) roughly doubled their constraint count and made proving
    # a tight bound much slower for the same schedule quality.
    cp_solver.parameters.relative_gap_limit  = 0.05

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
                'professor'       : primary.user.name if primary else '?',
                'staff_id'        : primary.staff_id  if primary else '?',
                'professor_id'    : s.primary_professor_id,
                'module_code'     : s.course.module_code,
                'module_title'    : s.course.title,
                'session_type'    : s.session_type,
                'day'             : ts.day_of_week,
                'period'          : ts.period_label,
                'time'            : f'{ts.start_time.strftime("%H:%M")}–{ts.end_time.strftime("%H:%M")}',
                'timeslot_id'     : ts.id,
                'declaration_id'  : decl.id if decl else None,
                'class_session_id': s.id,
            })

    # 7. Write TimetableEntry records
    TimetableEntry.query.filter_by(trimester=trimester, is_backbone=False).delete()

    # cal_weeks / non_break_weeks / ph_dates / event_blocks / _day_offset were
    # already computed above (step 2) so the domain-building step could steer
    # sessions away from days that would wipe them out entirely - reused here
    # unchanged rather than recomputed.

    entries = 0
    skipped_ph = 0
    skipped_ev = 0
    for s in sessions:
        ts      = timeslots[cp_solver.Value(slot_vars[s.id])]
        room_id = None
        if s.delivery_mode == 'f2f' and s.id in room_vars:
            room_id = rooms[cp_solver.Value(room_vars[s.id])].id

        day_offset = _day_offset.get(ts.day_of_week, 0)

        # Parse teaching_weeks into a set for fast lookup (None = all weeks)
        allowed_weeks = None
        if s.teaching_weeks:
            try:
                allowed_weeks = {int(w) for w in s.teaching_weeks.split(',') if w.strip()}
            except ValueError:
                allowed_weeks = None

        for week in non_break_weeks:
            # Skip weeks not in this session's teaching schedule
            if allowed_weeks is not None and week.week_number not in allowed_weeks:
                continue

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
        'sessions_async_skipped'    : len(async_sessions),
        'sessions_no_room_skipped'  : len(no_room_warnings),
        'no_room_warnings'          : no_room_warnings,
        'sessions_no_timeslot_skipped': len(no_timeslot_warnings),
        'no_timeslot_warnings'      : no_timeslot_warnings,
        'teaching_weeks'            : len(non_break_weeks),
        'entries_created'           : entries,
        'entries_skipped_ph'        : skipped_ph,
        'entries_skipped_events'    : skipped_ev,
        'solver_status'             : 'Optimal' if status == cp_model.OPTIMAL else 'Feasible',
        'strict_constraints_applied': strict_constraints_added,
        'preferred_violations'      : preferred_violations,
        'pins_applied'              : pins_applied,
        'pins_dropped'              : pins_dropped,
        'room_pins_applied'         : room_pins_applied,
        'room_pins_dropped'         : room_pins_dropped,
        'historical_honoured'       : sum(1 for nh, *_ in hist_penalty_vars if cp_solver.Value(nh) == 0),
        'historical_changed'        : sum(1 for nh, *_ in hist_penalty_vars if cp_solver.Value(nh) == 1),
        'preferred_ts_honoured'     : sum(1 for v in pref_ts_cost_vars if cp_solver.Value(v) == 0),
        'lec_tut_ordered'           : sum(1 for v in lec_tut_cost_vars if cp_solver.Value(v) == 0),
        'lec_lab_ordered'           : sum(1 for v in lec_lab_cost_vars if cp_solver.Value(v) == 0),
        'mode_switches_prof'        : sum(1 for v in mode_switch_prof_vars if cp_solver.Value(v) == 1),
        'mode_switches_group'       : sum(1 for v in mode_switch_grp_vars if cp_solver.Value(v) == 1),
        'prof_idle_gap_violations'  : sum(1 for v in prof_idle_gap_vars if cp_solver.Value(v) == 1),
        'group_backtoback_violations': sum(1 for v in group_backtoback_vars if cp_solver.Value(v) == 1),
        'room_util_violations'      : sum(1 for v in room_util_cost_vars if cp_solver.Value(v) == 1),
        'room_best_fit_wasted_seats': sum(cp_solver.Value(wv) for wv in room_best_fit_cost_vars),
        'consistent_venue_violations': (
            sum(1 for v in consistent_venue_prof_vars if cp_solver.Value(v) == 1)
            + sum(1 for v in consistent_venue_grp_vars if cp_solver.Value(v) == 1)
        ),
        'late_end_violations'       : sum(1 for v in late_end_cost_vars if cp_solver.Value(v) == 1),
        'extremal_slot_violations'  : sum(1 for v in extremal_cost_vars if cp_solver.Value(v) == 1),
        'day_spread_pairs'          : sum(1 for v in cluster_cost_vars if cp_solver.Value(v) == 1),
    }
