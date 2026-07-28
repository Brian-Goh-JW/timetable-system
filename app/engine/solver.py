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
FRI_EVENING_CUTOFF     = dtime(17, 0)   # Fri: no classes running past this time (stricter than the
                                         # general EVENING_CUTOFF below - confirmed 2026-07-18 against
                                         # the same source requirements another team's slides quoted
                                         # verbatim: "No Friday classes after 17:00")
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
WEIGHT_ONLINE_DAY_PATTERN = 10 # online session not on Mon/Tue, or not matching its
                                # programme's other online sessions' day (Ms. Yang's
                                # requirements doc: online classes clustered onto one
                                # Monday-or-Tuesday day per programme - confirmed
                                # 2026-07-18)

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
    'S12':       WEIGHT_ONLINE_DAY_PATTERN,
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


def _timeslots_overlap(a, b):
    """Return whether two catalogue slots overlap in real weekly time."""
    if a.day_of_week != b.day_of_week:
        return False
    a_start = a.start_time.hour * 60 + a.start_time.minute
    a_end = a.end_time.hour * 60 + a.end_time.minute
    b_start = b.start_time.hour * 60 + b.start_time.minute
    b_end = b.end_time.hour * 60 + b.end_time.minute
    return a_start < b_end and b_start < a_end


def _overlapping_timeslot_indices(timeslots, reference_slot):
    """Return every catalogue index that overlaps ``reference_slot``.

    Availability is expressed by selecting one catalogue slot, but catalogue
    slots are not disjoint (for example P2 12:00-14:00 overlaps Lab PM2
    13:00-15:00).  Expanding a declaration here keeps strict and preferred
    availability aligned with real clock time instead of exact row identity.
    """
    return {
        index for index, timeslot in enumerate(timeslots)
        if _timeslots_overlap(timeslot, reference_slot)
    }


def _room_compatible(room, session, group_size_override=None, require_capacity=True):
    """Room type must match session type and (unless require_capacity=False)
    capacity must fit the student group. group_size_override lets callers
    substitute the combined enrollment for sessions linked via a
    SharedModuleGroup (see solve()) instead of this session's own
    student_group size.

    require_capacity=False is retained for diagnostic callers only. Solver
    generation always checks capacity, including for explicitly pinned rooms,
    because a fixed assignment must not bypass a hard safety constraint."""
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
        group_size = session.effective_group_size if session.student_group else 1
    return room.capacity >= group_size


def _room_domain_with_fixed_pin(rooms, session, room_id_to_index, group_size_override=None):
    """Return the safe room domain and whether a fixed-room pin can be applied.

    A valid fixed room remains a hard pin.  A stale, inactive, wrong-type, or
    undersized fixed room is treated as bad source data: its pin is ignored and
    the session keeps every normally compatible room.  This prevents one bad
    imported venue from blocking the whole timetable without weakening room
    type or capacity constraints.
    """
    compatible = [
        i for i, room in enumerate(rooms)
        if _room_compatible(room, session, group_size_override)
    ]
    fixed_idx = room_id_to_index.get(session.fixed_room_id)
    if fixed_idx is not None and fixed_idx in compatible:
        return [fixed_idx], True
    return compatible, False


def _programme_session_components(sessions):
    """Group complete programmes without splitting shared-module classes.

    Every programme represented in ``sessions`` is atomic: all of its sessions
    are returned in one component. Programmes joined by a SharedModuleGroup are
    unioned into the same component so a combined class is never scheduled in
    separate solver runs.
    """
    by_programme = defaultdict(list)
    for session in sessions:
        by_programme[session.course.programme_id].append(session)

    parent = {programme_id: programme_id for programme_id in by_programme}

    def find(programme_id):
        while parent[programme_id] != programme_id:
            parent[programme_id] = parent[parent[programme_id]]
            programme_id = parent[programme_id]
        return programme_id

    def union(a, b):
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    shared_programmes = defaultdict(set)
    for session in sessions:
        if session.shared_module_group_id:
            shared_programmes[session.shared_module_group_id].add(
                session.course.programme_id
            )
    for programme_ids in shared_programmes.values():
        programme_ids = list(programme_ids)
        for i in range(1, len(programme_ids)):
            union(programme_ids[0], programme_ids[i])

    components = defaultdict(lambda: {'programme_ids': set(), 'programme_codes': set(), 'sessions': []})
    for programme_id, programme_sessions in by_programme.items():
        component = components[find(programme_id)]
        component['programme_ids'].add(programme_id)
        component['programme_codes'].add(programme_sessions[0].course.programme.code)
        component['sessions'].extend(programme_sessions)

    result = []
    for component in components.values():
        result.append({
            'programme_ids': sorted(component['programme_ids']),
            'programme_codes': sorted(component['programme_codes']),
            'session_ids': sorted(session.id for session in component['sessions']),
        })
    # Solve the largest/most connected component first. Later batches treat
    # earlier results as fixed occupancy, so placing tiny flexible programmes
    # first can unnecessarily corner a large constrained programme and make it
    # time out even though a complete ordering exists.
    return sorted(
        result,
        key=lambda item: (-len(item['session_ids']), item['programme_codes']),
    )


def _institutional_blocked_indices(timeslots):
    """
    Indices of timeslots blocked by SIT institutional policy:
      - Wednesday:   any slot starting at or after 13:00 (CCA afternoon policy)
      - Friday:      any slot starting at or after 12:00 and before 14:00
      - Friday:      any slot still running past 17:00 (stricter than the
                      general evening cutoff below - checked by END time, not
                      start time, since a slot starting before 17:00 but
                      running past it still puts students on campus after
                      the cutoff)
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
        if ts.day_of_week == 'Friday' and ts.end_time > FRI_EVENING_CUTOFF:
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


def _parallel_section_alternatives(left, right):
    """Whether two sessions are alternative sections of the same class.

    Template 2 stores parallel sections (for example ``T1``/``T2`` or
    ``P1``/``P2``) against one parent cohort because it has no per-student
    section-enrolment column.  Those rows must all be scheduled, but no one
    student attends every alternative.  Labels with different prefixes, or
    labels whose teaching weeks do not overlap, are not alternatives.
    """
    import re

    if left is None or right is None or left.id == right.id:
        return False
    if (left.course_id != right.course_id
            or left.session_type != right.session_type
            or left.student_group_id is None
            or left.student_group_id != right.student_group_id):
        return False

    left_match = re.fullmatch(r'([A-Z])(\d+)', left.group_label or '')
    right_match = re.fullmatch(r'([A-Z])(\d+)', right.group_label or '')
    return bool(
        left_match
        and right_match
        and left_match.group(1) == right_match.group(1)
        and left_match.group(2) != right_match.group(2)
        and _weeks_overlap(left, right)
    )


def _sessions_share_students(left, right):
    """Return whether the two session rows represent overlapping students."""
    if left is None or right is None:
        return False
    if not left.student_group_id or not right.student_group_id:
        return False
    if _parallel_section_alternatives(left, right):
        return False
    related_left = _conflicting_group_ids(left.student_group_id)
    related_right = _conflicting_group_ids(right.student_group_id)
    return (
        right.student_group_id in related_left
        or left.student_group_id in related_right
    )


def _collapse_for_overlap(sess_list):
    """Collapse only records representing the same jointly taught class.

    Independent sessions remain distinct even when both are fixed to the same
    timeslot; AddNoOverlap must see both so that contradictory hard inputs make
    the model infeasible instead of disappearing from conflict detection.
    """
    seen = set()
    out = []
    for session in sess_list:
        if session.shared_module_group_id is not None:
            key = ('shared', session.shared_module_group_id)
        else:
            key = ('unique', session.id)
        if key not in seen:
            seen.add(key)
            out.append(session)
    return out


def _build_group_session_families(sessions):
    """Return parent/sub-group session families for hard student rules."""
    by_group = defaultdict(list)
    for session in sessions:
        if session.student_group_id:
            by_group[session.student_group_id].append(session)

    group_parent = {group_id: group_id for group_id in by_group}

    def find(group_id):
        while group_parent[group_id] != group_id:
            group_parent[group_id] = group_parent[group_parent[group_id]]
            group_id = group_parent[group_id]
        return group_id

    def union(a, b):
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            group_parent[root_a] = root_b

    for group_id in by_group:
        for other_group_id in _conflicting_group_ids(group_id):
            if other_group_id in by_group:
                union(group_id, other_group_id)

    families = defaultdict(list)
    for group_id, group_sessions in by_group.items():
        families[find(group_id)].extend(group_sessions)
    return families


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

    existing = (AcademicCalendar.query
                .filter_by(trimester=trimester)
                .order_by(AcademicCalendar.week_number)
                .all())
    expected_weeks = list(range(1, 14))
    needs_rebuild = (
        [week.week_number for week in existing] != expected_weeks
        or (existing and existing[0].start_date != start_date)
        or any(
            week.is_term_break != (week.week_number in term_break_weeks)
            for week in existing
        )
    )
    if needs_rebuild:
        AcademicCalendar.query.filter_by(trimester=trimester).delete(
            synchronize_session=False
        )
        existing = []

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
          pinned_slots=None, historical_preferred=None, session_id_filter=None,
          occupied_trimester=None, append_to_existing=False, max_time_seconds=400):
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
        session_id_filter  : set[int]|None - if given, only schedule ClassSessions with an id in this
                                              set (still subject to the normal readiness/trimester_num
                                              filters). Used for large-trimester two-phase solving: a first
                                              pass schedules just the "bridge" sessions (professors/shared
                                              modules spanning multiple programmes, whose mutual NoOverlap
                                              constraints are what make the full trimester's search
                                              intractable in one CP-SAT call - found 2026-07-18), and their
                                              result is then fed back in as pinned_slots for a second,
                                              full-trimester call. Every hard constraint (lunch window,
                                              strict availability, room capacity, etc.) still applies in
                                              full during this first pass - it's the exact same solve()
                                              logic on a smaller session set, not a relaxed approximation.
        occupied_trimester : str|None - generated entries under this key are treated as fixed
                                      resource occupancy. Used by staged programme-batch generation.
        append_to_existing : bool - only replace entries for session_id_filter instead of clearing
                                    the whole trimester. Intended for a unique staging trimester.
        max_time_seconds   : int|float - bounded CP-SAT search time for this solve call.

    Returns:
        (success: bool, message: str, stats: dict)
    """
    if term_break_weeks is None:
        term_break_weeks = DEFAULT_TERM_BREAK_WEEKS

    # 1. Load every session in scope. Each synchronous session needs a student
    # group: without one, its generated entry cannot appear in a student's
    # timetable, so treating the run as successful would be misleading.
    filters = [
        # Deliberately excluded from this generation pass (see bootstrap/48-49
        # and the "Deferred from T1 generation" note on System Info) - not a
        # data gap, a disclosed scope decision made when the full trimester
        # was too large/interconnected for CP-SAT to solve in any practical
        # time. Reversible: clearing the flag restores full scope.
        ClassSession.deferred_from_solve.is_(False),
    ]
    # Filter by trimester number if specified (1, 2, or 3)
    if trimester_num is not None:
        filters.append(ClassSession.trimester == trimester_num)
    if session_id_filter is not None:
        filters.append(ClassSession.id.in_(session_id_filter))

    all_sessions = ClassSession.query.filter(*filters).all()

    missing_group_sessions = [
        s for s in all_sessions
        if not s.is_async and s.student_group_id is None
    ]
    if missing_group_sessions:
        labels = ', '.join(
            f'{s.course.module_code} ({s.session_type})'
            for s in missing_group_sessions[:5]
        )
        suffix = '...' if len(missing_group_sessions) > 5 else ''
        return False, (
            f'Generation blocked: {len(missing_group_sessions)} synchronous session(s) '
            f'have no student group and would be invisible to students: {labels}{suffix}'
        ), {
            'sessions_missing_group': len(missing_group_sessions),
        }

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

    # Earlier successful programme batches are stored under a unique staging
    # trimester. Collapse their per-week TimetableEntry rows into recurring
    # assignments so this solve can treat them as fixed resource occupancy.
    occupied_assignments = []
    if occupied_trimester:
        occupied_rows = (TimetableEntry.query
                         .filter_by(trimester=occupied_trimester, is_backbone=False)
                         .all())
        grouped_occupied = {}
        for entry in occupied_rows:
            key = (
                entry.class_session_id,
                entry.timeslot_id,
                entry.room_id,
                entry.override_professor_id,
            )
            assignment = grouped_occupied.get(key)
            if assignment is None:
                other_session = entry.class_session
                professor_ids = set(other_session.all_professor_ids)
                if entry.override_professor_id:
                    professor_ids.add(entry.override_professor_id)
                assignment = {
                    'session': other_session,
                    'timeslot': entry.timeslot,
                    'room_id': entry.room_id,
                    'professor_ids': professor_ids,
                    'student_group_id': other_session.student_group_id,
                    'weeks': set(),
                }
                grouped_occupied[key] = assignment
            assignment['weeks'].add(entry.week_number)
        occupied_assignments = list(grouped_occupied.values())

    default_week_numbers = {week.week_number for week in non_break_weeks}
    session_week_numbers = {}
    for session in sessions:
        if session.teaching_weeks:
            try:
                weeks = {
                    int(week) for week in session.teaching_weeks.split(',')
                    if week.strip()
                }
            except ValueError:
                weeks = set(default_week_numbers)
        else:
            weeks = set(default_week_numbers)
        session_week_numbers[session.id] = weeks

    conflicting_group_ids = {}

    def _shares_staged_person_or_group(session, assignment):
        other = assignment['session']
        if (session.shared_module_group_id
                and session.shared_module_group_id == other.shared_module_group_id):
            return False
        if set(session.all_professor_ids) & assignment['professor_ids']:
            return True
        if not session.student_group_id or not assignment['student_group_id']:
            return False
        if session.student_group_id not in conflicting_group_ids:
            conflicting_group_ids[session.student_group_id] = _conflicting_group_ids(
                session.student_group_id
            )
        return assignment['student_group_id'] in conflicting_group_ids[session.student_group_id]

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
        if s.fixed_timeslot_id:
            fixed_idx = ts_id_to_index.get(s.fixed_timeslot_id)
            if fixed_idx is None:
                no_timeslot_warnings.append(
                    f'{s.course.module_code} ({s.session_type}) - fixed timeslot '
                    f'{s.fixed_timeslot_id} no longer exists.'
                )
                continue
            if fixed_idx not in compat:
                ts = timeslots[fixed_idx]
                no_timeslot_warnings.append(
                    f'{s.course.module_code} ({s.session_type}, {s.duration_hours}h) - '
                    f'fixed slot {ts.day_of_week} {ts.period_label} has an incompatible duration.'
                )
                continue
            compat = [fixed_idx]
        elif not compat:
            no_timeslot_warnings.append(
                f'{s.course.module_code} ({s.session_type}, {s.duration_hours}h) - '
                f'no time slot of this length exists in the system.'
            )
            continue
        if occupied_assignments:
            blocked_indices = set()
            session_weeks = session_week_numbers[s.id]
            for assignment in occupied_assignments:
                if not (session_weeks & assignment['weeks']):
                    continue
                if not _shares_staged_person_or_group(s, assignment):
                    continue
                blocked_indices.update(
                    i for i in compat
                    if _timeslots_overlap(timeslots[i], assignment['timeslot'])
                )
            compat = [i for i in compat if i not in blocked_indices]
            if not compat:
                no_timeslot_warnings.append(
                    f'{s.course.module_code} ({s.session_type}) - every matching slot conflicts '
                    f'with a professor or student group already scheduled in another programme.'
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
                f"fall outside this trimester's {len(non_break_weeks)}-week calendar."
            )
            continue
        compat_after_calendar = [i for i in compat if not _all_occurrences_blocked(s, timeslots[i])]
        if not compat_after_calendar:
            no_timeslot_warnings.append(
                f'{s.course.module_code} ({s.session_type}) - every candidate day/time falls on a '
                f"public holiday or cancelled event for this session's teaching week(s) "
                f'({s.teaching_weeks}).'
            )
            continue
        compat_slots[s.id] = compat_after_calendar

    if no_timeslot_warnings:
        return False, (
            f'Generation blocked: {len(no_timeslot_warnings)} session(s) have no valid '
            f'timeslot. {no_timeslot_warnings[0]}'
        ), {
            'sessions_no_timeslot_skipped': len(no_timeslot_warnings),
            'no_timeslot_warnings': no_timeslot_warnings,
        }

    # Combined enrollment for sessions linked via a SharedModuleGroup (Common
    # Modules / Programme Grouping) - room must fit ALL linked programmes'
    # students together, not just this one session's own group.
    shared_group_combined_size = {}   # shared_module_group_id -> combined intake_size
    for s in sessions:
        if s.shared_module_group_id and s.student_group:
            shared_group_combined_size[s.shared_module_group_id] = (
                shared_group_combined_size.get(s.shared_module_group_id, 0)
                + s.effective_group_size
            )

    def _session_group_size(session):
        """Enrollment used consistently by room hard and soft constraints."""
        return shared_group_combined_size.get(
            session.shared_module_group_id,
            session.effective_group_size if session.student_group else 1,
        )

    compat_rooms = {}           # session.id → [room indices]  (f2f only)
    no_room_warnings = []       # sessions skipped due to room capacity
    for s in sessions:
        if s.delivery_mode == 'f2f':
            size_override = shared_group_combined_size.get(s.shared_module_group_id)
            compat, _fixed_room_is_compatible = _room_domain_with_fixed_pin(
                rooms, s, room_id_to_index, size_override,
            )
            if not compat:
                group_size = _session_group_size(s)
                no_room_warnings.append(
                    f'{s.course.module_code} ({s.session_type}, group size {group_size}): '
                    f'no compatible room.'
                )
            else:
                compat_rooms[s.id] = compat

    if no_room_warnings:
        return False, (
            f'Generation blocked: {len(no_room_warnings)} session(s) have no compatible '
            f'room. {no_room_warnings[0]}'
        ), {
            'sessions_no_room_skipped': len(no_room_warnings),
            'no_room_warnings': no_room_warnings,
        }

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
        declaration_slot = d.timeslot
        if declaration_slot is not None:
            strict_blocked[d.professor_id].update(
                _overlapping_timeslot_indices(timeslots, declaration_slot)
            )

    # professor_id → set of timeslot indices that are soft-avoided
    preferred_avoided = defaultdict(set)
    for d in preferred_decls:
        declaration_slot = d.timeslot
        if declaration_slot is not None:
            preferred_avoided[d.professor_id].update(
                _overlapping_timeslot_indices(timeslots, declaration_slot)
            )

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

    # Warm-start hint: seed slot_vars/room_vars from this trimester's existing
    # (pre-this-solve) TimetableEntry data, if any - a genuine soft hint via
    # AddHint (not a hard pin like pinned_slots/fixed_timeslot_id above), so
    # CP-SAT starts its search from an assignment that already satisfies the
    # vast majority of constraints and only needs to repair the smaller
    # number of real conflicts, instead of searching from nothing. Found
    # 2026-07-18: after the wall-clock-overlap fix (hard constraints A/B/C
    # rewritten to use AddNoOverlap), T1 couldn't find even one feasible
    # point in 400s with only 8 of 205,513 variables hinted (a handful of
    # backbone-linked sessions from historical_preferred below) - CP-SAT was
    # essentially searching blind across ~537 interacting sessions.
    existing_entries = (TimetableEntry.query
                         .filter_by(trimester=trimester, is_backbone=False)
                         .all())
    existing_slot_by_session = {}
    existing_room_by_session = {}
    for e in existing_entries:
        if e.class_session_id not in existing_slot_by_session and e.timeslot_id:
            existing_slot_by_session[e.class_session_id] = e.timeslot_id
        if e.class_session_id not in existing_room_by_session and e.room_id:
            existing_room_by_session[e.class_session_id] = e.room_id
    for s in sessions:
        # Skip slot_vars here if historical_preferred will hint this session
        # too (below) - CP-SAT rejects (MODEL_INVALID) a variable hinted
        # twice, and the historical/backbone-preferred slot is the more
        # deliberate choice of the two anyway.
        if not (historical_preferred and s.id in historical_preferred):
            ts_id = existing_slot_by_session.get(s.id)
            if ts_id is not None:
                idx = ts_id_to_index.get(ts_id)
                if idx is not None and idx in compat_slots[s.id]:
                    model.AddHint(slot_vars[s.id], idx)
        if s.id in room_vars:
            room_id = existing_room_by_session.get(s.id)
            if room_id is not None:
                ridx = room_id_to_index.get(room_id)
                if ridx is not None and ridx in compat_rooms[s.id]:
                    model.AddHint(room_vars[s.id], ridx)

    # Shared BoolVar cache: (session_id, ts_idx) -> "session placed at that slot".
    # Moved up (was previously built just before the lunch-window constraint)
    # so hard constraint C's wall-clock-overlap room check can reuse it too.
    _slot_at_vars = {}

    def _get_slot_at(session_id, ts_idx):
        key = (session_id, ts_idx)
        if key not in _slot_at_vars:
            b = model.NewBoolVar(f'lat_{session_id}_{ts_idx}')
            model.Add(slot_vars[session_id] == ts_idx).OnlyEnforceIf(b)
            model.Add(slot_vars[session_id] != ts_idx).OnlyEnforceIf(b.Not())
            _slot_at_vars[key] = b
        return _slot_at_vars[key]

    # A room used by an earlier staged programme is unavailable only for
    # overlapping weeks and wall-clock times. Professor/group conflicts were
    # removed from slot domains above; room conflicts depend on both decision
    # variables and are therefore represented as forbidden (slot, room) pairs.
    staged_room_forbidden_pairs = 0
    if occupied_assignments:
        for s in sessions:
            if s.id not in room_vars:
                continue
            forbidden = set()
            session_weeks = session_week_numbers[s.id]
            for assignment in occupied_assignments:
                room_idx = room_id_to_index.get(assignment['room_id'])
                if room_idx is None or room_idx not in compat_rooms[s.id]:
                    continue
                other = assignment['session']
                if (s.shared_module_group_id
                        and s.shared_module_group_id == other.shared_module_group_id):
                    continue
                if not (session_weeks & assignment['weeks']):
                    continue
                for slot_idx in compat_slots[s.id]:
                    if _timeslots_overlap(timeslots[slot_idx], assignment['timeslot']):
                        forbidden.add((slot_idx, room_idx))
            if forbidden:
                model.AddForbiddenAssignments(
                    [slot_vars[s.id], room_vars[s.id]], sorted(forbidden)
                )
                staged_room_forbidden_pairs += len(forbidden)

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

    # ---- Interval-based double-booking prevention (hard constraints A/B/C) --
    # Every session gets a "wall-clock start minute" derived from its slot_var
    # (day_index*1440 + minutes-into-day, so Monday and Tuesday can never
    # collide) and a fixed-duration CP-SAT interval built from it. Professor/
    # group/room double-booking is then enforced with AddNoOverlap - CP-SAT's
    # purpose-built "none of these may overlap in time" constraint - instead
    # of comparing TimeSlot row identity (wrong: the catalog has multiple
    # rows sharing a start time for different durations, e.g. a 2h lecture
    # and a 3h lab both starting Monday 09:00 - found 2026-07-17 via a
    # professor/room/group conflict audit against an exported Template 2:
    # 391 room, 351 professor, 1077 student-group conflicts in one trimester)
    # or decomposing into thousands of pairwise sub-constraints (tried next -
    # scaled to 194k variables/2.2M constraints on this system's ~537-session
    # trimesters and made CP-SAT unable to find any solution at all, found
    # 2026-07-18). AddNoOverlap uses native scheduling propagation instead of
    # pairwise enumeration, so it stays cheap at this system's real scale.
    _DAY_INDEX = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4}
    week_start_minutes = [
        _DAY_INDEX[ts.day_of_week] * 1440 + ts.start_time.hour * 60 + ts.start_time.minute
        for ts in timeslots
    ]
    HORIZON_MINUTES = 5 * 1440

    start_var = {}
    interval_var = {}
    for s in sessions:
        sv = model.NewIntVar(0, HORIZON_MINUTES, f'start_{s.id}')
        model.AddElement(slot_vars[s.id], week_start_minutes, sv)
        start_var[s.id] = sv
        dur_min = s.duration_hours * 60
        interval_var[s.id] = model.NewIntervalVar(sv, dur_min, sv + dur_min, f'ivl_{s.id}')

    def _weeks_clusters(sess_list):
        """Partition sess_list via union-find over _weeks_overlap edges, so
        sessions with genuinely non-overlapping teaching weeks (e.g. one
        module split into a weeks-1-8 lecture and a weeks-9-13 lecture) don't
        get forced apart just because AddNoOverlap has no concept of specific
        calendar weeks, only a recurring weekly pattern. A safe over-
        approximation when a cluster isn't a perfect partition - grouping a
        few extra sessions together only makes the model more conservative,
        never incorrect. _weeks_overlap is a cheap in-memory set check, no
        DB/CP-SAT calls, so this stays fast even for an entity with many
        sessions."""
        parent = {s.id: s.id for s in sess_list}
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        n = len(sess_list)
        for i in range(n):
            for j in range(i + 1, n):
                if _weeks_overlap(sess_list[i], sess_list[j]):
                    union(sess_list[i].id, sess_list[j].id)
        clusters = defaultdict(list)
        for s in sess_list:
            clusters[find(s.id)].append(s)
        return list(clusters.values())

    def _add_no_overlap_for_entity(sess_list):
        for cluster in _weeks_clusters(_collapse_for_overlap(sess_list)):
            if len(cluster) >= 2:
                model.AddNoOverlap([interval_var[s.id] for s in cluster])

    # Hard constraint A - no professor double-booking (all professors incl. co-teachers)
    by_prof = defaultdict(list)
    for s in sessions:
        for prof_id in s.all_professor_ids:
            by_prof[prof_id].append(s)
    for sess_list in by_prof.values():
        _add_no_overlap_for_entity(sess_list)

    # Hard constraint B - no student-group double-booking (parent/child aware)
    # Groups that can conflict with each other (a group + its parent + its
    # sub-groups) are merged into one family first via union-find over
    # _conflicting_group_ids, so the whole family's sessions get one
    # AddNoOverlap call together instead of a separate check per group pair.
    families = _build_group_session_families(sessions)
    for sess_list in families.values():
        reduced = _collapse_for_overlap(sess_list)
        has_parallel_sections = any(
            _parallel_section_alternatives(left, right)
            for index, left in enumerate(reduced)
            for right in reduced[index + 1:]
        )
        if not has_parallel_sections:
            _add_no_overlap_for_entity(reduced)
            continue

        # A single AddNoOverlap cannot express "all pairs except alternative
        # sections".  Use compact two-interval constraints only for these
        # exceptional cohort families; ordinary families keep the faster
        # aggregate propagator above.
        for index, left in enumerate(reduced):
            for right in reduced[index + 1:]:
                if (_weeks_overlap(left, right)
                        and _sessions_share_students(left, right)):
                    model.AddNoOverlap([
                        interval_var[left.id], interval_var[right.id]
                    ])

    # Hard constraint C - no room double-booking
    # Room assignment is itself a decision variable, so sessions can't be
    # statically grouped by room the way professor/group can. Instead, for
    # each physical room, build an OPTIONAL interval per eligible session
    # (present only when that session's room_vars actually picks this room)
    # and let one AddNoOverlap per room enforce non-overlap among whichever
    # sessions end up placed there.
    f2f_reduced = _collapse_for_overlap(
        [s for s in sessions if s.delivery_mode == 'f2f' and s.id in room_vars]
    )
    room_eligible = defaultdict(list)   # room_idx -> [sessions eligible for it]
    for s in f2f_reduced:
        for r in compat_rooms.get(s.id, []):
            room_eligible[r].append(s)
    for room_idx, sess_list in room_eligible.items():
        if len(sess_list) < 2:
            continue
        optional_intervals = []
        for s in sess_list:
            is_here = model.NewBoolVar(f'roomhere_{room_idx}_{s.id}')
            model.Add(room_vars[s.id] == room_idx).OnlyEnforceIf(is_here)
            model.Add(room_vars[s.id] != room_idx).OnlyEnforceIf(is_here.Not())
            dur_min = s.duration_hours * 60
            optional_intervals.append(model.NewOptionalIntervalVar(
                start_var[s.id], dur_min, start_var[s.id] + dur_min, is_here,
                f'roomivl_{room_idx}_{s.id}'
            ))
        model.AddNoOverlap(optional_intervals)

    # Hard constraint D - fixed timeslot pins
    # Priority: fixed_timeslot_id (admin-set permanent pin) > pinned_slots (Option A carry-over)
    # A permanent fixed slot and strict professor unavailability are both hard
    # constraints. If they conflict, surface the data conflict instead of
    # silently weakening either rule. Carry-over pins remain optional.
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

        is_strictly_blocked = any(
            pinned_idx in strict_blocked.get(prof_id, set())
            for prof_id in s.all_professor_ids
        )
        if is_strictly_blocked:
            if s.fixed_timeslot_id:
                ts = timeslots[pinned_idx]
                return False, (
                    f'Generation blocked: fixed slot {ts.day_of_week} '
                    f'{ts.period_label} for {s.course.module_code} conflicts '
                    f'with a professor strict-unavailability declaration.'
                ), {}
            pins_dropped += 1
            continue

        model.Add(slot_vars[s.id] == pinned_idx)
        pins_applied += 1

    # Hard constraint D2 - fixed room pins (from cleaned-data venue codes)
    # A bad imported venue must not make the entire model infeasible.  Valid
    # pins were reduced to a one-room domain above; invalid/missing pins retain
    # only normally compatible alternatives and are counted as dropped here.
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

    # _get_slot_at / _slot_at_vars were defined earlier (right after
    # slot_vars/room_vars) so hard constraint C could reuse them too; still
    # used below by the lunch-window hard constraint and soft constraints
    # S1/S2 (mode-switch) and the group back-to-back constraint.

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

    # Use the same parent/sub-group families as hard non-overlap. A subgroup
    # student attends both parent-cohort and subgroup sessions, so lunch must
    # be evaluated against the union of those sessions.
    for grp_id, grp_sess in families.items():
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

    # Soft constraint S12 - cluster online classes onto one Monday-or-Tuesday
    # day per programme. Only synchronous online sessions reach here at all
    # (is_async sessions are filtered out of `sessions` before slot/day
    # assignment even begins, near the top of solve() - they have no
    # meaningful "day" to cluster). Two components, both soft since forcing
    # either as a hard rule could make some programmes' real data infeasible:
    #   (a) penalise a synchronous online session landing on a day that
    #       isn't Monday or Tuesday
    #   (b) penalise two of the same programme's online sessions landing on
    #       DIFFERENT days from each other
    online_wrong_day_vars = []
    online_by_prog = defaultdict(list)
    for s in sessions:
        if s.delivery_mode == 'online':
            online_by_prog[s.course.programme_id].append(s)

    _MON_TUE_DAYS = {0, 1}
    not_mon_tue_map = [0 if slot_to_day[i] in _MON_TUE_DAYS else 1 for i in range(len(timeslots))]
    for s in sessions:
        if s.delivery_mode != 'online':
            continue
        s_slots = list(compat_slots[s.id])
        if not any(not_mon_tue_map[i] for i in s_slots):
            continue  # every compatible slot is already Mon/Tue - nothing to penalise
        if all(not_mon_tue_map[i] for i in s_slots):
            continue  # no Mon/Tue option exists at all - penalty would be a constant, useless
        wrong_day = model.NewIntVar(0, 1, f's12_wrongday_{s.id}')
        model.AddElement(slot_vars[s.id], not_mon_tue_map, wrong_day)
        online_wrong_day_vars.append(wrong_day)

    online_day_mismatch_vars = []
    for _prog_id, sess_list in online_by_prog.items():
        n = len(sess_list)
        for i in range(n):
            for j in range(i + 1, n):
                si, sj = sess_list[i], sess_list[j]
                mismatch = model.NewBoolVar(f's12_daymismatch_{si.id}_{sj.id}')
                model.Add(day_var_map[si.id] != day_var_map[sj.id]).OnlyEnforceIf(mismatch)
                model.Add(day_var_map[si.id] == day_var_map[sj.id]).OnlyEnforceIf(mismatch.Not())
                online_day_mismatch_vars.append(mismatch)

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
        group_size = _session_group_size(s)
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
        group_size = _session_group_size(s)
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
    for v in online_wrong_day_vars:
        obj_terms.append(W['S12'] * v)
    for v in online_day_mismatch_vars:
        obj_terms.append(W['S12'] * v)

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
    cp_solver.parameters.max_time_in_seconds = max(1, float(max_time_seconds))
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
        status_name = cp_solver.StatusName(status)
        return False, (
            f'CP-SAT ended with {status_name} for this programme batch. '
            'It may have no valid combination under the hard constraints, or may need more '
            'search time. The batch was left out without affecting successful programmes.'
        ), {
            'solver_status': status_name,
            'failure_kind': status_name.lower(),
            'sessions_attempted': len(sessions),
        }

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
    if append_to_existing and session_id_filter is not None:
        (TimetableEntry.query
         .filter(
             TimetableEntry.trimester == trimester,
             TimetableEntry.is_backbone.is_(False),
             TimetableEntry.class_session_id.in_(session_id_filter),
         )
         .delete(synchronize_session=False))
    else:
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
        'occupied_assignments_considered': len(occupied_assignments),
        'staged_room_forbidden_pairs': staged_room_forbidden_pairs,
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
        'online_wrong_day_violations': sum(1 for v in online_wrong_day_vars if cp_solver.Value(v) == 1),
        'online_day_mismatch_violations': sum(1 for v in online_day_mismatch_vars if cp_solver.Value(v) == 1),
    }
