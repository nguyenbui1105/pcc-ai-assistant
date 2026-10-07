"""Schedule optimizer: conflict detection, prereq filter, credit targeting."""
import re
import unicodedata
from dataclasses import dataclass, field

from src.schedule.parser import CourseSection

# ── Day parsing ───────────────────────────────────────────────────────────────

_DAY_ALIASES: dict[str, str] = {
    "monday": "Mon", "mon": "Mon", "m": "Mon",
    "tuesday": "Tue", "tue": "Tue", "tues": "Tue", "tu": "Tue",
    "wednesday": "Wed", "wed": "Wed", "weds": "Wed", "w": "Wed",
    "thursday": "Thu", "thu": "Thu", "thur": "Thu", "thurs": "Thu", "th": "Thu",
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
        if w not in _DAY_ALIASES and w.endswith("days"):
            w = w[:-1]  # plural: 'fridays' → 'friday'
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


def section_meetings(sec: CourseSection) -> list[tuple[str, str]]:
    """(days, time) of every meeting of a section: the main row plus extra rows such as labs."""
    return [(sec.days, sec.time), *getattr(sec, "extra_meetings", [])]


def is_in_person(sec: CourseSection) -> bool:
    """False for sections with no campus meetings ('Online', 'Remote' scheduled online meetings)."""
    return not sec.section_type.strip().lower().startswith(("online", "remote"))


def sections_conflict(a: CourseSection, b: CourseSection) -> bool:
    """True if any meeting of a overlaps any meeting of b (same day AND time)."""
    return any(
        _meetings_conflict(days_a, time_a, days_b, time_b)
        for days_a, time_a in section_meetings(a)
        for days_b, time_b in section_meetings(b)
    )


def _meetings_conflict(a_days: str, a_time: str, b_days: str, b_time: str) -> bool:
    days_a = parse_days(a_days)
    days_b = parse_days(b_days)

    # Online sections never conflict with anything
    if not days_a or not days_b:
        return False

    shared_days = days_a & days_b
    if not shared_days:
        return False

    time_a = parse_time_to_minutes(a_time)
    time_b = parse_time_to_minutes(b_time)

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


# Day lists in preferences: "no Fridays", "can't do Tue or Thu", "không học thứ 3 và thứ 5".
# Matched against accent-folded text (_fold), so Vietnamese works with or without diacritics.
_EN_DAY = r"(?:mon|tues?|wed(?:nes|s)?|thu(?:rs?)?|fri|sat(?:ur)?|sun)(?:days?)?\b"
_VI_DAY_NUM = r"(?:[2-7]|hai|ba|tu|nam|sau|bay)\b"
_VI_DAY_WORDS = {
    "2": "Mon", "hai": "Mon", "3": "Tue", "ba": "Tue", "4": "Wed", "tu": "Wed",
    "5": "Thu", "nam": "Thu", "6": "Fri", "sau": "Fri", "7": "Sat", "bay": "Sat",
}
_VI_DAY = (
    rf"thu\s*{_VI_DAY_NUM}"
    # "thứ 2, 4, 6" — but not "thứ 6, 3 môn" / "thứ 6, 12 tín chỉ"
    r"(?:\s*(?:,|va\b|hoac\b|hay\b|/|&)\s*[2-7](?![\d:])"
    r"(?!\s*(?:tin|mon|lop|gio|tieng|h|cr|credits?|courses?|classes?|am|pm)\b))*"
    r"|chu\s*nhat|cn\b"
)
_DAY_ITEM = rf"\b(?:{_VI_DAY}|{_EN_DAY})"
_DAY_SEP = r"\s*(?:,|/|&|\+|\band\b|\bor\b|\bva\b|\bhoac\b|\bhay\b)(?:\s*(?:and|or|va|hoac)\b)?\s*"
# A listed day followed by "is fine" / "works" starts a new clause: "no Friday, Monday is fine"
_DAY_OK = r"(?!\s+(?:(?:is|are)\s+)?(?:works?|fine|ok|okay|good|better|best|preferred)\b)"
_DAY_LIST = rf"({_DAY_ITEM}(?:{_DAY_SEP}{_DAY_ITEM}{_DAY_OK})*)"
_DAY_NEG_BEFORE = (
    r"\b(?:not available|unavailable|nothing|busy|not|no|can[’']?t|cannot|can not|don[’']?t want"
    r"|avoid|except|khong|ko|ban|nghi|tru|tranh)"
    # filler words between the negation and the days: "can't make it on", "không học vào"
    r"(?:\s+(?:on|do|make|it|attend|take|have|any|every|class(?:es)?|courses?|school|at|in|the"
    r"|(?:morning|afternoon|evening|night)s?|co|di|hoc|lop|vao|ngay|cac|buoi|sang|chieu|toi"
    r"|duoc|muon))*\s+"
)
_DAY_NEG_AFTER = (
    r"\s+(?:(?:is|are)\s+(?:out|bad)|(?:(?:is|are)\s+)?(?:not available|not good|no good)"
    r"|(?:doesn[’']?t|don[’']?t|does not|do not) work)\b"
)


def _fold(text: str) -> str:
    """Lowercase and strip Vietnamese diacritics: 'Không học Thứ 6' → 'khong hoc thu 6'."""
    decomposed = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _parse_pref_days(day_list: str) -> set[str]:
    """Map a captured day list ('fridays or sat', 'thu 2, 4', 'chu nhat') to day names."""
    days: set[str] = set()
    for item in re.finditer(_DAY_ITEM, day_list):
        word = item.group(0)
        if word.startswith(("chu", "cn")):
            days.add("Sun")
        elif re.match(rf"thu\s*{_VI_DAY_NUM}", word):
            days |= {_VI_DAY_WORDS[n] for n in re.findall(rf"\b{_VI_DAY_NUM}", word[3:])}
        else:
            days |= parse_days(word)
    return days


def parse_prefs(text: str, is_international: bool = False) -> SchedulePrefs:
    """Extract schedule preferences from free-text user input (English or Vietnamese)."""
    prefs = SchedulePrefs()
    text_lower = _fold(text)

    # Credits — "12 credits", "a 12-credit schedule", "12 tín chỉ"
    m = re.search(r"\b(\d+)[\s-]*(?:credits?|cr|tin\s*chi)\b", text_lower)
    if m:
        prefs.credit_target = int(m.group(1))
    elif re.search(r"\bfull[\s-]?time\b|toan\s*thoi\s*gian", text_lower):
        prefs.credit_target = 12
    elif re.search(r"\bpart[\s-]?time\b|ban\s*thoi\s*gian", text_lower):
        prefs.credit_target = 8

    # Mode preference ("trực tuyến" = online, "trực tiếp" = in-person)
    if re.search(r"\bonline\b|truc\s*tuyen", text_lower):
        prefs.mode = "online"
    elif re.search(r"\bin.?person\b|truc\s*tiep", text_lower):
        prefs.mode = "in-person"

    # Blocked days — "not available X" / "no Xs" / "can't do X" / "X doesn't work" / "không học thứ N".
    # Only day names are captured, so "can't do Tuesday but Thursday is fine" blocks Tuesday only.
    for pat in (_DAY_NEG_BEFORE + _DAY_LIST, _DAY_LIST + _DAY_NEG_AFTER):
        for hit in re.finditer(pat, text_lower):
            prefs.blocked_days |= _parse_pref_days(hit.group(1))

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
        if prefs.mode == "online" and is_in_person(sec):
            continue
        if prefs.mode == "in-person" and not is_in_person(sec):
            continue

        # Blocked days and time window apply to every meeting (a Friday lab breaks "no Friday")
        if not all(_meeting_fits(days, time, prefs) for days, time in section_meetings(sec)):
            continue

        out.append(sec)
    return out


def _meeting_fits(days: str, time: str, prefs: SchedulePrefs) -> bool:
    meeting_days = parse_days(days)
    if meeting_days & prefs.blocked_days:
        return False

    # Time window (only applies to meetings with scheduled days)
    if meeting_days:
        time_range = parse_time_to_minutes(time)
        if time_range:
            start, end = time_range
            if start < prefs.earliest_hour or end > prefs.latest_hour:
                return False
    return True


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

    # Deduplicate: one section per course (prefer in-person if mode=any, else first).
    # Keyed by normalize_code so variants of one course ('WR122' / 'WR122Z') count once.
    course_best: dict[str, CourseSection] = {}
    for sec in eligible:
        code = normalize_code(sec.course_code)
        if code not in course_best:
            course_best[code] = sec
        else:
            existing = course_best[code]
            # Prefer in-person when mode is "any"
            if prefs.mode == "any" and is_in_person(sec) and not is_in_person(existing):
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
        inperson_pool = [s for s in ok_pool if is_in_person(s)]
        online_pool   = [s for s in ok_pool if not is_in_person(s)]
        # Fill in-person: target the 9cr minimum but allow up to credit_target+2
        # so 4cr courses aren't skipped when we're at 8cr (old bug: max was 9+2=11).
        best = _greedy_pick(inperson_pool, 9, max_credits=prefs.credit_target + 2)
        if best is None:
            best = _greedy_pick(inperson_pool, prefs.credit_target, max_credits=prefs.credit_target + 2)
        # Every in-person candidate is prereq-flagged: start from those (flagged) instead of no plan
        if best is None:
            best = _greedy_pick(
                [s for s in missing_pool if is_in_person(s)], 9, max_credits=prefs.credit_target + 2
            )
        # No in-person section at all: return the online plan; validation flags the F-1 shortfall
        if best is None:
            best = _greedy_pick(online_pool, prefs.credit_target)
        # Top up to credit_target with online if needed.
        # Pass credit_target (not gap) as absolute total target for _greedy_pick.
        if best is not None and best.total_credits < prefs.credit_target:
            extra = _greedy_pick(online_pool, prefs.credit_target, existing=best.sections)
            if extra:
                best.sections.extend(extra.sections)
                best.total_credits += extra.total_credits
        # Still short (no online pool, e.g. mode=in-person): keep adding in-person courses
        if best is not None and best.total_credits < prefs.credit_target:
            extra = _greedy_pick(inperson_pool, prefs.credit_target, existing=best.sections)
            if extra:
                best.sections.extend(extra.sections)
                best.total_credits += extra.total_credits
        # If still short of the F-1 minimums (9 in-person / 12 total), try missing-prereq
        # in-person courses (flagged)
        if best is not None:
            inperson_credits = sum(
                section_credits(s) for s in best.sections if is_in_person(s)
            )
            if inperson_credits < 9 or best.total_credits < 12:
                more = _greedy_pick(
                    [s for s in missing_pool if is_in_person(s)],
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

        # Every candidate is prereq-flagged: build from them (flagged) instead of no plan
        if best is None:
            best = _greedy_pick(missing_pool, prefs.credit_target)
        # If we're short on credits, fill with missing-prereq courses (flagged)
        elif best.credit_gap(prefs.credit_target) > 2:
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
        if sec in selected:  # pool may overlap existing (F-1 in-person top-up)
            continue
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
