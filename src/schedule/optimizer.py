"""Schedule optimizer: conflict detection, prereq filter, credit targeting."""
import re
from dataclasses import dataclass, field
from itertools import combinations

from src.schedule.parser import CourseSection

# ── Day parsing ───────────────────────────────────────────────────────────────

_DAY_ALIASES: dict[str, str] = {
    "monday": "Mon", "mon": "Mon", "m": "Mon",
    "tuesday": "Tue", "tue": "Tue", "tu": "Tue",
    "wednesday": "Wed", "wed": "Wed", "w": "Wed",
    "thursday": "Thu", "thu": "Thu", "th": "Thu",
    "friday": "Fri", "fri": "Fri", "f": "Fri",
    "saturday": "Sat", "sat": "Sat", "sa": "Sat",
    "sunday": "Sun", "sun": "Sun", "su": "Sun",
}

_ALL_DAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}


def parse_days(days_str: str) -> set[str]:
    """Parse 'Monday and Wednesday' or 'Tue+Thu' → {'Tue', 'Thu'}."""
    if not days_str or "online" in days_str.lower():
        return set()  # online = no fixed days
    words = re.split(r"[\s,+/&]+|and", days_str.lower())
    result: set[str] = set()
    for w in words:
        w = w.strip().rstrip(".")
        if w in _DAY_ALIASES:
            result.add(_DAY_ALIASES[w])
    return result


def parse_time_to_minutes(time_str: str) -> tuple[int, int] | None:
    """Parse '8am-4:50pm' → (480, 1010) in minutes since midnight. None if online."""
    if not time_str or "not applicable" in time_str.lower():
        return None
    m = re.search(
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*[-–]\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
        time_str, re.I,
    )
    if not m:
        # Single time like "10am" — treat as 1-hour block
        m2 = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", time_str, re.I)
        if not m2:
            return None
        h, mn, meridiem = int(m2.group(1)), int(m2.group(2) or 0), m2.group(3).lower()
        start = (h % 12) * 60 + mn + (720 if meridiem == "pm" else 0)
        return start, start + 60

    def to_min(h: str, mn: str, mer: str) -> int:
        hh, mm = int(h), int(mn or 0)
        return (hh % 12) * 60 + mm + (720 if mer.lower() == "pm" else 0)

    start = to_min(m.group(1), m.group(2), m.group(3))
    end   = to_min(m.group(4), m.group(5), m.group(6))
    return start, end


def sections_conflict(a: CourseSection, b: CourseSection) -> bool:
    """True if two sections have overlapping days AND times."""
    days_a = parse_days(a.days)
    days_b = parse_days(b.days)

    # Online sections never conflict with anything
    if not days_a or not days_b:
        return False

    shared_days = days_a & days_b
    if not shared_days:
        return False

    time_a = parse_time_to_minutes(a.time)
    time_b = parse_time_to_minutes(b.time)

    if time_a is None or time_b is None:
        return False

    # Overlap check: [start_a, end_a) ∩ [start_b, end_b) ≠ ∅
    return time_a[0] < time_b[1] and time_b[0] < time_a[1]


# ── User preferences ──────────────────────────────────────────────────────────

@dataclass
class SchedulePrefs:
    credit_target: int = 9
    mode: str = "any"              # "online" | "in-person" | "any"
    blocked_days: set[str] = field(default_factory=set)
    earliest_hour: int = 0         # minutes since midnight (0 = no restriction)
    latest_hour: int = 1440        # 1440 = midnight
    is_international: bool = False  # F-1: min 12cr total, min 9cr in-person


def parse_prefs(text: str, is_international: bool = False) -> SchedulePrefs:
    """Extract schedule preferences from free-text user input."""
    prefs = SchedulePrefs()
    text_lower = text.lower()

    # Credits
    m = re.search(r"\b(\d+)\s*credits?\b", text_lower)
    if m:
        prefs.credit_target = int(m.group(1))
    elif "full.?time" in text_lower or "full time" in text_lower:
        prefs.credit_target = 12
    elif "part.?time" in text_lower or "part time" in text_lower:
        prefs.credit_target = 8

    # Mode preference
    if re.search(r"\bonline\b", text_lower):
        prefs.mode = "online"
    elif re.search(r"\bin.?person\b", text_lower):
        prefs.mode = "in-person"

    # Blocked days — look for "not available X" / "no X" / "can't X"
    neg_patterns = [
        r"(?:not available|no|can'?t|busy|unavailable)\s+(?:on\s+)?([a-z ,+/&]+day\w*)",
        r"(?:except|avoid)\s+([a-z ,+/&]+day\w*)",
        r"([a-z ,+/&]+day\w*)\s+(?:not available|is out|doesn't work|no good)",
    ]
    for pat in neg_patterns:
        for hit in re.finditer(pat, text_lower):
            prefs.blocked_days |= parse_days(hit.group(1))

    # Earliest hour — "not before 10am", "nothing before 9am", "after 8am", "start from 10am"
    m = re.search(
        r"(?:not before|nothing before|no class before|no earlier than|earliest|start from|after|from)\s+"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
        text_lower,
    )
    if m:
        h, mn, mer = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        prefs.earliest_hour = (h % 12) * 60 + mn + (720 if mer == "pm" else 0)

    # Latest hour — "done by 5pm", "finish by 4pm", "out by 3pm", "no later than 6pm"
    m = re.search(
        r"(?:done by|finish by|end by|out by|no later than|be done by)\s+"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
        text_lower,
    )
    if m:
        h, mn, mer = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        prefs.latest_hour = (h % 12) * 60 + mn + (720 if mer == "pm" else 0)

    # International student rules: F-1 requires min 12cr, min 9cr in-person
    prefs.is_international = is_international
    if is_international:
        prefs.credit_target = max(prefs.credit_target, 12)
        # Cannot be fully online — they need at least 9 in-person
        if prefs.mode == "online":
            prefs.mode = "any"  # will be enforced in build_schedule

    return prefs


# ── Section filtering ─────────────────────────────────────────────────────────

def filter_by_prefs(sections: list[CourseSection], prefs: SchedulePrefs) -> list[CourseSection]:
    """Remove sections that violate user preferences."""
    out: list[CourseSection] = []
    for sec in sections:
        if not sec.is_open:
            continue

        # Mode filter
        if prefs.mode == "online" and sec.section_type != "Online":
            continue
        if prefs.mode == "in-person" and sec.section_type == "Online":
            continue

        # Blocked days
        sec_days = parse_days(sec.days)
        if sec_days & prefs.blocked_days:
            continue

        # Time window (only applies to in-person sections)
        if sec_days:  # has scheduled days = not fully online
            time_range = parse_time_to_minutes(sec.time)
            if time_range:
                start, end = time_range
                if start < prefs.earliest_hour or end > prefs.latest_hour:
                    continue

        out.append(sec)
    return out


# ── Credit estimation ─────────────────────────────────────────────────────────

def estimate_credits(code: str) -> int:
    """Estimate credits from course code number (≥200 = 4cr, else 3cr default)."""
    m = re.search(r"\d+", code)
    if m and int(m.group()) >= 200:
        return 4
    return 3  # safe default


def section_credits(sec: CourseSection) -> int:
    if sec.credits:
        try:
            return int(float(sec.credits))
        except ValueError:
            pass
    return estimate_credits(sec.course_code)


# ── Schedule builder ──────────────────────────────────────────────────────────

@dataclass
class SchedulePlan:
    sections: list[CourseSection]
    total_credits: int
    missing_prereqs: dict[str, list[str]]   # course_code → missing prereqs

    def credit_gap(self, target: int) -> int:
        return target - self.total_credits


def build_schedule(
    candidates: list[CourseSection],
    prefs: SchedulePrefs,
    completed_courses: list[str],
) -> SchedulePlan | None:
    """
    Greedily pick one section per course (deduplicated), no conflicts,
    closest to credit_target without exceeding by more than 2.
    """
    from src.schedule.prereq_map import get_missing_prereqs, get_superseded_courses, normalize_code

    # Filter by prefs first
    eligible = filter_by_prefs(candidates, prefs)

    # Remove already-completed courses and courses "below" what student has done
    # e.g. WR121 done → exclude WR121 itself and WR115 (superseded prereq)
    # normalize_code strips leading zeros: 'MTH065' == 'MTH65'
    completed_norm = {normalize_code(c) for c in completed_courses}
    superseded = get_superseded_courses(completed_courses)  # already normalized
    eligible = [
        s for s in eligible
        if normalize_code(s.course_code) not in completed_norm
        and normalize_code(s.course_code) not in superseded
    ]

    # Deduplicate: one section per course_code (prefer in-person if mode=any, else first)
    course_best: dict[str, CourseSection] = {}
    for sec in eligible:
        code = sec.course_code
        if code not in course_best:
            course_best[code] = sec
        else:
            existing = course_best[code]
            # Prefer in-person when mode is "any"
            if prefs.mode == "any" and sec.section_type != "Online" and existing.section_type == "Online":
                course_best[code] = sec

    pool = list(course_best.values())

    if not pool:
        return None

    # Check prereqs for each candidate
    missing_map: dict[str, list[str]] = {}
    for sec in pool:
        missing = get_missing_prereqs(sec.course_code, completed_courses)
        if missing:
            missing_map[sec.course_code] = missing

    # Separate prereq-ok from prereq-missing
    ok_pool = [s for s in pool if s.course_code not in missing_map]
    missing_pool = [s for s in pool if s.course_code in missing_map]

    # For international students: prioritize in-person to meet 9cr in-person minimum.
    # Split pool into in-person and online, pick in-person first.
    if prefs.is_international:
        inperson_pool = [s for s in ok_pool if s.section_type != "Online"]
        online_pool   = [s for s in ok_pool if s.section_type == "Online"]
        # Fill in-person: target the 9cr minimum but allow up to credit_target+2
        # so 4cr courses aren't skipped when we're at 8cr (old bug: max was 9+2=11).
        best = _greedy_pick(inperson_pool, 9, max_credits=prefs.credit_target + 2)
        if best is None:
            best = _greedy_pick(inperson_pool, prefs.credit_target, max_credits=prefs.credit_target + 2)
        # Top up to credit_target with online if needed.
        # Pass credit_target (not gap) as absolute total target for _greedy_pick.
        if best is not None and best.total_credits < prefs.credit_target:
            extra = _greedy_pick(online_pool, prefs.credit_target, existing=best.sections)
            if extra:
                best.sections.extend(extra.sections)
                best.total_credits += extra.total_credits
        # If still short on in-person, try missing-prereq in-person courses
        if best is not None:
            inperson_credits = sum(
                section_credits(s) for s in best.sections if s.section_type != "Online"
            )
            if inperson_credits < 9:
                more = _greedy_pick(
                    [s for s in missing_pool if s.section_type != "Online"],
                    prefs.credit_target,
                    existing=best.sections,
                )
                if more:
                    best.sections.extend(more.sections)
                    best.total_credits += more.total_credits
                    best.missing_prereqs.update(missing_map)
    else:
        # Try to build from ok_pool first
        best = _greedy_pick(ok_pool, prefs.credit_target)

        # If we're short on credits, fill with missing-prereq courses (flagged)
        if best is not None and best.credit_gap(prefs.credit_target) > 2:
            extra = _greedy_pick(missing_pool, prefs.credit_target, existing=best.sections)
            if extra:
                best.sections.extend(extra.sections)
                best.total_credits += extra.total_credits
                best.missing_prereqs.update(missing_map)

    if best:
        best.missing_prereqs = {
            s.course_code: missing_map[s.course_code]
            for s in best.sections
            if s.course_code in missing_map
        }

    return best


def _greedy_pick(
    pool: list[CourseSection],
    target: int,
    existing: list[CourseSection] | None = None,
    max_credits: int | None = None,
) -> SchedulePlan | None:
    """Pick non-conflicting sections summing close to target credits.

    max_credits overrides the default (target + 2) overshoot guard.
    Use max_credits=prefs.credit_target+2 in the F-1 in-person fill phase
    so 4cr courses aren't dropped when we need exactly 9cr in-person
    but are already at 8cr (8+4=12 would be blocked by the old 9+2=11 guard).
    """
    selected: list[CourseSection] = list(existing or [])
    total = sum(section_credits(s) for s in selected)
    ceiling = max_credits if max_credits is not None else target + 2

    for sec in pool:
        if total >= target:
            break
        cr = section_credits(sec)
        if total + cr > ceiling:
            continue
        # Conflict check against already selected
        if any(sections_conflict(sec, s) for s in selected):
            continue
        selected.append(sec)
        total += cr

    if not selected:
        return None

    own = [s for s in selected if s not in (existing or [])]
    return SchedulePlan(
        sections=own,
        total_credits=sum(section_credits(s) for s in own),
        missing_prereqs={},
    )
