"""Regression tests for optimizer / preference fixes (findings 6, 8, 9, 10, 11, 12, 13, 15).

No network: the schedule fetch is replaced and prereq lookups are stubbed where a test
must not depend on the contents of the PREREQS table.
"""
import pytest

import src.agent.schedule_planner as SP
import src.schedule.prereq_map as prereq_map
from src.academic.constraint_validator import validate_plan
from src.mypcc.parser import DegreeAudit, DegreeRequirement
from src.schedule.optimizer import (
    SchedulePlan,
    SchedulePrefs,
    build_schedule,
    filter_by_prefs,
    is_in_person,
    parse_days,
    parse_prefs,
    sections_conflict,
)
from src.schedule.parser import CourseSection


def _sec(code, days="", time="", stype="In-person", credits="4", crn="10000", extra=None):
    sec = CourseSection(course_code=code, title=code, crn=crn, seats=1,
                        section_type=stype, days=days, time=time, credits=credits)
    if extra is not None:
        sec.extra_meetings = extra  # CourseSection.extra_meetings (schedule-parser contract)
    return sec


@pytest.fixture
def no_prereqs(monkeypatch):
    """Treat every candidate as prerequisite-ok."""
    monkeypatch.setattr(prereq_map, "get_missing_prereqs", lambda code, completed: [])


@pytest.fixture
def all_prereqs_missing(monkeypatch):
    """Flag every candidate as missing a prerequisite."""
    monkeypatch.setattr(prereq_map, "get_missing_prereqs", lambda code, completed: ["XX100"])


# ── Finding 6: extra meeting rows (labs) ──────────────────────────────────────

def test_sections_conflict_checks_extra_meetings():
    g201 = _sec("G201", "Tuesday and Thursday", "9am–10:50am",
                extra=[("Thursday", "3pm–5:50pm")])
    phl201 = _sec("PHL201", "Tuesday and Thursday", "3pm–4:50pm")
    assert sections_conflict(g201, phl201)
    assert sections_conflict(phl201, g201)


def test_sections_conflict_extra_meeting_without_overlap():
    g201 = _sec("G201", "Tuesday and Thursday", "9am–10:50am",
                extra=[("Thursday", "3pm–5:50pm")])
    phl201 = _sec("PHL201", "Monday and Wednesday", "3pm–4:50pm")
    assert not sections_conflict(g201, phl201)


def test_filter_by_prefs_blocked_day_on_extra_meeting():
    phy121 = _sec("PHY121", "Monday and Wednesday", "10am–11:50am",
                  extra=[("Friday", "1pm–3:50pm")])
    assert filter_by_prefs([phy121], SchedulePrefs(blocked_days={"Fri"})) == []
    assert filter_by_prefs([phy121], SchedulePrefs(blocked_days={"Tue"})) == [phy121]


def test_filter_by_prefs_time_window_on_extra_meeting():
    ch221 = _sec("CH221", "Monday and Wednesday", "10am–11:50am",
                 extra=[("Wednesday", "6pm–8:50pm")])
    assert filter_by_prefs([ch221], SchedulePrefs(latest_hour=17 * 60)) == []


def test_build_schedule_avoids_lab_conflict(no_prereqs):
    g201 = _sec("G201", "Tuesday and Thursday", "9am–10:50am", crn="40101",
                extra=[("Thursday", "3pm–5:50pm")])
    phl201 = _sec("PHL201", "Tuesday and Thursday", "3pm–4:50pm", crn="40201")
    plan = build_schedule([g201, phl201], SchedulePrefs(credit_target=8), [])
    assert [s.course_code for s in plan.sections] == ["G201"]


def test_format_plan_shows_extra_meetings():
    phy121 = _sec("PHY121", "Monday and Wednesday", "10am–11:50am",
                  extra=[("Friday", "1pm–3:50pm")])
    plan = SchedulePlan(sections=[phy121], total_credits=4, missing_prereqs={})
    out = SP._format_plan(plan, SchedulePrefs(credit_target=4), "Fall 2026", [])
    assert "Monday and Wednesday 10am–11:50am" in out
    assert "Friday 1pm–3:50pm" in out


# ── Finding 8: every candidate prereq-flagged → still build a (flagged) plan ──

_FLAGGED = [
    ("ENG104", "Monday and Wednesday", "10am–11:50am", "4"),
    ("CH104", "Tuesday and Thursday", "9am–10:50am", "5"),
    ("BI211", "Monday and Wednesday", "1pm–2:50pm", "5"),
]


def test_build_schedule_all_flagged_domestic(all_prereqs_missing):
    cands = [_sec(c, d, t, credits=cr) for c, d, t, cr in _FLAGGED]
    plan = build_schedule(cands, SchedulePrefs(credit_target=9), [])
    assert plan is not None and plan.total_credits >= 9
    assert set(plan.missing_prereqs) == {s.course_code for s in plan.sections}


def test_build_schedule_all_flagged_f1(all_prereqs_missing):
    cands = [_sec(c, d, t, credits=cr) for c, d, t, cr in _FLAGGED]
    prefs = SchedulePrefs(credit_target=12, is_international=True)
    plan = build_schedule(cands, prefs, [])
    assert plan is not None
    assert plan.total_credits >= 12
    assert not [v for v in validate_plan(plan, prefs) if v.is_hard]
    assert set(plan.missing_prereqs) == {s.course_code for s in plan.sections}


@pytest.mark.anyio
async def test_no_plan_message_does_not_blame_conflicts(monkeypatch):
    # The only open section is a course the student already completed.
    async def fake_collect(audit, term, profile=None):
        return [_sec("PHL201", "Monday and Wednesday", "9am–10:50am")]

    monkeypatch.setattr(SP, "_collect_sections", fake_collect)
    audit = DegreeAudit(degree="Associate of Arts Oregon Transfer (AAOT)",
                        requirements=[DegreeRequirement("Arts and Letters", "incomplete")],
                        completed_courses=["PHL201"])
    out = await SP.plan_schedule_async(audit, "4 credits", term="fall2026")
    assert "conflict-free" not in out
    assert "open sections" in out


# ── Finding 9: F-1 "in-person only" keeps adding in-person courses past 9 cr ──

def _f1_pool():
    return [
        _sec("MTH112", "Monday, Tuesday, Wednesday, and Thursday", "12pm–12:50pm", credits="5"),
        _sec("PHL201", "Tuesday and Thursday", "8am–9:50am"),
        _sec("WR227", "Friday", "9am–10:50am"),
        _sec("PHL202", stype="Online"),
    ]


def test_f1_in_person_only_reaches_12_credits(no_prereqs):
    prefs = SchedulePrefs(credit_target=12, mode="in-person", is_international=True)
    plan = build_schedule(_f1_pool(), prefs, [])
    assert plan.total_credits >= 12
    assert all(is_in_person(s) for s in plan.sections)
    assert not [v for v in validate_plan(plan, prefs) if v.is_hard]


def test_f1_any_mode_without_online_sections_reaches_12_credits(no_prereqs):
    prefs = SchedulePrefs(credit_target=12, is_international=True)
    plan = build_schedule(_f1_pool()[:3], prefs, [])
    assert plan.total_credits >= 12


def test_f1_any_mode_still_prefers_online_top_up(no_prereqs):
    prefs = SchedulePrefs(credit_target=12, is_international=True)
    plan = build_schedule(_f1_pool(), prefs, [])
    assert "PHL202" in [s.course_code for s in plan.sections]
    assert plan.total_credits >= 12


# ── Finding 10: "Remote" sections are not in-person ───────────────────────────

@pytest.mark.parametrize("stype, expected", [
    ("Online", False),
    ("Remote", False),
    ("Remote (scheduled online meetings)", False),
    ("In-person", True),
    ("Hybrid", True),
    ("", True),
])
def test_is_in_person(stype, expected):
    assert is_in_person(_sec("X", stype=stype)) is expected


def test_validate_plan_counts_remote_as_online():
    plan = SchedulePlan(sections=[
        _sec("MTH112", "Monday and Wednesday", "12pm–12:50pm", "Remote", credits="5"),
        _sec("PHY121", "Monday and Wednesday", "10am–11:50am", "Remote"),
        _sec("WR227", stype="Online"),
    ], total_credits=13, missing_prereqs={})
    prefs = SchedulePrefs(credit_target=12, is_international=True)
    assert "INSUFFICIENT_INPERSON" in [v.code for v in validate_plan(plan, prefs)]
    out = SP._format_plan(plan, prefs, "Fall 2026", [])
    assert "0 in-person · 13 online" in out
    assert "Meets F-1 requirement" not in out


def test_filter_by_prefs_mode_with_remote():
    remote = _sec("MTH112", "Monday and Wednesday", "12pm–12:50pm", "Remote")
    assert filter_by_prefs([remote], SchedulePrefs(mode="in-person")) == []
    assert filter_by_prefs([remote], SchedulePrefs(mode="online")) == [remote]


def test_f1_build_schedule_fills_in_person_before_remote(no_prereqs):
    cands = [
        _sec("MTH112", "Monday and Wednesday", "12pm–12:50pm", "Remote", credits="5"),
        _sec("PHL201", "Tuesday and Thursday", "8am–9:50am"),
        _sec("WR227", "Friday", "9am–10:50am"),
        _sec("ART116", "Monday and Wednesday", "2pm–3:50pm"),
    ]
    prefs = SchedulePrefs(credit_target=12, is_international=True)
    plan = build_schedule(cands, prefs, [])
    inperson = sum(int(s.credits) for s in plan.sections if is_in_person(s))
    assert inperson >= 9


def test_f1_remote_only_still_returns_plan_for_compliance_warning(no_prereqs):
    cands = [
        _sec("MTH112", "Monday and Wednesday", "12pm–12:50pm", "Remote", credits="5"),
        _sec("PHY121", "Tuesday and Thursday", "10am–11:50am", "Remote"),
        _sec("WR227", stype="Online"),
    ]
    prefs = SchedulePrefs(credit_target=12, is_international=True)
    plan = build_schedule(cands, prefs, [])
    assert plan is not None
    assert "INSUFFICIENT_INPERSON" in [v.code for v in validate_plan(plan, prefs)]


# ── Findings 11-13: parse_prefs ───────────────────────────────────────────────

@pytest.mark.parametrize("text, target, blocked, mode", [
    # Finding 11: Vietnamese
    ("12 tín chỉ, không học thứ 6", 12, {"Fri"}, "any"),
    ("15 tín chỉ", 15, set(), "any"),
    ("6 tín chỉ, chỉ học online, không học thứ 6", 6, {"Fri"}, "online"),
    ("12 tin chi, khong hoc thu 6", 12, {"Fri"}, "any"),            # no diacritics
    ("không học thứ 3 và thứ 5", 9, {"Tue", "Thu"}, "any"),
    ("không học thứ 2, 4, 6 nhé", 9, {"Mon", "Wed", "Fri"}, "any"),
    ("không học thứ 6, 3 môn thôi", 9, {"Fri"}, "any"),
    ("không học vào chủ nhật", 9, {"Sun"}, "any"),
    ("bận thứ Hai", 9, {"Mon"}, "any"),
    ("học trực tuyến", 9, set(), "online"),
    ("toàn thời gian, học trực tiếp", 12, set(), "in-person"),
    # Finding 12: plural day names, negation captures only day names
    ("12 credits, no Fridays", 12, {"Fri"}, "any"),
    ("9 credits, can't do Tuesdays or Thursdays", 9, {"Tue", "Thu"}, "any"),
    ("I can't do Tuesday but Thursday is fine", 9, {"Tue"}, "any"),
    ("no early mornings, I prefer Tuesday and Thursday", 9, set(), "any"),
    ("nothing on Friday", 9, {"Fri"}, "any"),
    ("no Friday, Monday is fine", 9, {"Fri"}, "any"),
    ("Fridays don't work", 9, {"Fri"}, "any"),
    ("Monday is fine but Friday is out", 9, {"Fri"}, "any"),
    ("Tuesday and Thursday are out", 9, {"Tue", "Thu"}, "any"),
    ("not available tues/thurs", 9, {"Tue", "Thu"}, "any"),
    ("I can’t make it on Mondays", 9, {"Mon"}, "any"),
    # unchanged behaviour
    ("not available Tuesday and Thursday", 9, {"Tue", "Thu"}, "any"),
    ("9 credits, prefer online, not available Tuesday morning", 9, {"Tue"}, "online"),
    ("12 credits, no Friday, done by 4pm, prefer online", 12, {"Fri"}, "online"),
    ("any day except Friday", 9, {"Fri"}, "any"),
    # Finding 13: full-time / part-time / N-credit
    ("full-time, in-person", 12, set(), "in-person"),
    ("full time", 12, set(), "any"),
    ("part-time please", 8, set(), "any"),
    ("a 12-credit schedule", 12, set(), "any"),
])
def test_parse_prefs(text, target, blocked, mode):
    p = parse_prefs(text)
    assert (p.credit_target, p.blocked_days, p.mode) == (target, blocked, mode)


@pytest.mark.parametrize("s, expected", [
    ("Fridays", {"Fri"}),
    ("Tuesdays and Thursdays", {"Tue", "Thu"}),
    ("Tues/Thurs", {"Tue", "Thu"}),
])
def test_parse_days_plural_and_abbreviations(s, expected):
    assert parse_days(s) == expected


# ── Finding 15: course variants compared through normalize_code ───────────────

def _z_stripping_normalize(code: str) -> str:
    """The canonical normalize_code contract: 'WR 122Z' → 'WR122'."""
    import re
    m = re.match(r"^([A-Z]+)0*(\d+)([A-Y]?)Z?$", code.upper().replace(" ", "").replace("-", ""))
    return f"{m.group(1)}{m.group(2)}{m.group(3)}" if m else code.strip().upper()


def test_build_schedule_dedups_course_variants(monkeypatch, no_prereqs):
    monkeypatch.setattr(prereq_map, "normalize_code", _z_stripping_normalize)
    cands = [
        _sec("WR122", "Monday and Wednesday", "9am–10:50am"),
        _sec("WR122Z", "Tuesday and Thursday", "9am–10:50am"),
    ]
    plan = build_schedule(cands, SchedulePrefs(credit_target=8), [])
    assert len(plan.sections) == 1


def test_build_schedule_excludes_z_variant_of_completed(monkeypatch, no_prereqs):
    monkeypatch.setattr(prereq_map, "normalize_code", _z_stripping_normalize)
    cands = [_sec("WR122Z", "Monday and Wednesday", "9am–10:50am")]
    assert build_schedule(cands, SchedulePrefs(credit_target=4), ["WR121", "WR122"]) is None
