"""Tests for src/schedule/optimizer.py"""
import pytest
from src.schedule.optimizer import (
    parse_days,
    parse_time_to_minutes,
    sections_conflict,
    parse_prefs,
    SchedulePrefs,
    section_credits,
    filter_by_prefs,
)
from src.schedule.parser import CourseSection


# ── parse_days ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("s, expected", [
    ("Monday and Wednesday", {"Mon", "Wed"}),
    ("Tuesday and Thursday", {"Tue", "Thu"}),
    ("Tue+Thu", {"Tue", "Thu"}),
    ("Friday", {"Fri"}),
    ("online", set()),
    ("", set()),
    ("Saturday", {"Sat"}),
])
def test_parse_days(s, expected):
    assert parse_days(s) == expected


# ── parse_time_to_minutes ─────────────────────────────────────────────────────

def test_parse_time_range():
    result = parse_time_to_minutes("8am-4:50pm")
    assert result == (480, 1010)

def test_parse_time_noon():
    result = parse_time_to_minutes("10am-12pm")
    assert result == (600, 720)

def test_parse_time_online():
    assert parse_time_to_minutes("Not applicable") is None
    assert parse_time_to_minutes("") is None


# ── sections_conflict ─────────────────────────────────────────────────────────

def _sec(days: str, time: str, stype: str = "In-person") -> CourseSection:
    return CourseSection(
        course_code="TEST", title="T", crn="12345",
        days=days, time=time, section_type=stype,
    )

def test_conflict_same_day_overlapping():
    a = _sec("Monday and Wednesday", "10am-11am")
    b = _sec("Monday and Wednesday", "10:30am-11:30am")
    assert sections_conflict(a, b)

def test_no_conflict_different_days():
    a = _sec("Monday", "10am-11am")
    b = _sec("Tuesday", "10am-11am")
    assert not sections_conflict(a, b)

def test_no_conflict_adjacent_times():
    a = _sec("Monday", "9am-10am")
    b = _sec("Monday", "10am-11am")
    assert not sections_conflict(a, b)

def test_no_conflict_online():
    a = _sec("", "Not applicable", "Online")
    b = _sec("Monday", "10am-11am")
    assert not sections_conflict(a, b)


# ── parse_prefs ───────────────────────────────────────────────────────────────

def test_parse_prefs_credits():
    p = parse_prefs("I want 12 credits this term")
    assert p.credit_target == 12

def test_parse_prefs_online():
    p = parse_prefs("online only")
    assert p.mode == "online"

def test_parse_prefs_blocked_days():
    p = parse_prefs("not available Tuesday and Thursday")
    assert "Tue" in p.blocked_days
    assert "Thu" in p.blocked_days

def test_parse_prefs_earliest():
    p = parse_prefs("nothing before 10am")
    assert p.earliest_hour == 600  # 10 * 60

def test_parse_prefs_latest():
    p = parse_prefs("done by 5pm")
    assert p.latest_hour == 17 * 60

def test_parse_prefs_international_min12():
    p = parse_prefs("9 credits", is_international=True)
    assert p.credit_target == 12  # enforced to 12

def test_parse_prefs_international_online_override():
    p = parse_prefs("online only", is_international=True)
    assert p.mode == "any"  # online-only not allowed for F-1

def test_parse_prefs_fulltime():
    p = parse_prefs("I want full time")
    assert p.credit_target == 12


# ── section_credits ───────────────────────────────────────────────────────────

def test_section_credits_from_field():
    s = CourseSection(course_code="BI231", credits="4")
    assert section_credits(s) == 4

def test_section_credits_decimal():
    s = CourseSection(course_code="PE101", credits="1.0")
    assert section_credits(s) == 1

def test_section_credits_estimate_high():
    s = CourseSection(course_code="MTH251")  # >= 200 → 4cr
    assert section_credits(s) == 4

def test_section_credits_estimate_low():
    s = CourseSection(course_code="WR115")  # < 200 → 3cr
    assert section_credits(s) == 3


# ── filter_by_prefs ───────────────────────────────────────────────────────────

def _open_sec(code: str, days: str, time: str, stype: str = "In-person") -> CourseSection:
    s = CourseSection(course_code=code, title="T", crn="99999",
                      days=days, time=time, section_type=stype, seats=1)
    return s

def test_filter_blocks_closed():
    prefs = SchedulePrefs()
    closed = CourseSection(course_code="X", seats=0)
    result = filter_by_prefs([closed], prefs)
    assert result == []

def test_filter_online_mode():
    prefs = SchedulePrefs(mode="online")
    online = _open_sec("X", "", "Not applicable", "Online")
    inperson = _open_sec("Y", "Monday", "10am-11am", "In-person")
    result = filter_by_prefs([online, inperson], prefs)
    assert len(result) == 1
    assert result[0].course_code == "X"

def test_filter_blocked_days():
    prefs = SchedulePrefs(blocked_days={"Tue"})
    tue = _open_sec("A", "Tuesday", "10am-11am")
    mon = _open_sec("B", "Monday", "10am-11am")
    result = filter_by_prefs([tue, mon], prefs)
    assert len(result) == 1
    assert result[0].course_code == "B"

def test_filter_time_window():
    prefs = SchedulePrefs(earliest_hour=600, latest_hour=900)  # 10am–3pm
    early = _open_sec("A", "Monday", "8am-9am")
    ok = _open_sec("B", "Monday", "10am-11am")
    result = filter_by_prefs([early, ok], prefs)
    assert len(result) == 1
    assert result[0].course_code == "B"
