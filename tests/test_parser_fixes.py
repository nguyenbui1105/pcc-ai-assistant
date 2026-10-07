"""Regression tests for src/schedule/parser.py: start-time am/pm, continuation rows, Remote type."""
import pytest
from bs4 import BeautifulSoup

from src.schedule.optimizer import parse_time_to_minutes, sections_conflict
from src.schedule.parser import CourseSection, _parse_days_time, parse_detail_html


def _cell(html: str):
    return BeautifulSoup(f"<table><tr><td>{html}</td></tr></table>", "html.parser").td


def _row(crn: str, stype: str, days_title: str, time_html: str) -> str:
    days_cell = (
        f'<td class="days"><div class="weekly" title="Class on {days_title}" aria-hidden="true"></div> '
        f"{time_html}</td>"
    ) if days_title else '<td class="days">— <span class="visually-hidden">Not applicable</span></td>'
    return (
        f'<tr data-crn="{crn}" data-seats="1" data-fees="0.00"><th scope="row">{crn}</th>'
        f"<td>{stype}</td><td>Sylvania Campus</td>{days_cell}"
        f'<td><span aria-hidden="true">Sep 28</span><span aria-hidden="true">&ndash;</span>'
        f'<span aria-hidden="true">Dec 12</span></td>'
        f'<td class="seats"></td><td><a href="#">Staff</a></td><td></td></tr>'
    )


def _page(*rows: str) -> str:
    return (
        '<h1>G 201 Physical Geology</h1><p class="credits"><strong>Credits: 4</strong></p>'
        f'<table class="crn-table"><tbody>{"".join(rows)}</tbody></table>'
    )


# ── Start-time am/pm ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected_time, expected_minutes", [
    # Meridiem printed on both ends: the start's own am/pm must be kept
    ("from 11am - to 12:50pm", "11am–12:50pm", (660, 770)),
    ('<span class="visually-hidden">from</span> 11:00am '
     '<span class="visually-hidden">- to</span> 12:50pm', "11:00am–12:50pm", (660, 770)),
    ("11am – 12:50pm", "11am–12:50pm", (660, 770)),
    ("11:00 am - 12:50 pm", "11:00am–12:50pm", (660, 770)),
    ("8am-4:50pm", "8am–4:50pm", (480, 1010)),
    ("from 9am - to 12:50pm", "9am–12:50pm", (540, 770)),
    # No meridiem on the start: crossing noon means the start is am
    ("from 11 - to 12:50pm", "11am–12:50pm", (660, 770)),
    ("from 10:30 - to 12:20pm", "10:30am–12:20pm", (630, 740)),
    # No meridiem on the start, same half of the day: unchanged behaviour
    ("from 9:30 - to 11:50am", "9:30am–11:50am", (570, 710)),
    ("from 12 - to 1:50pm", "12pm–1:50pm", (720, 830)),
    ("from 6 - to 9:50pm", "6pm–9:50pm", (1080, 1310)),
])
def test_parse_days_time_start_meridiem(text, expected_time, expected_minutes):
    days, time_str = _parse_days_time(
        _cell(f'<div class="weekly" title="Class on Tuesday and Thursday"></div> {text}')
    )
    assert days == "Tuesday and Thursday"
    assert time_str == expected_time
    assert parse_time_to_minutes(time_str) == expected_minutes


def test_detail_page_noon_crossing_section_conflicts():
    # MTH243 TR 11:00–12:50 overlaps MTH112 MTWR 12:00–12:50 on Tue/Thu
    html = _page(_row("30231", "In-person lecture", "Tuesday and Thursday",
                      '<span class="visually-hidden">from</span> 11am '
                      '<span class="visually-hidden">- to</span> 12:50pm'))
    [mth243] = parse_detail_html(html, "MTH243", "Statistics I")
    assert mth243.time == "11am–12:50pm"
    mth112 = CourseSection(course_code="MTH112", crn="30221", seats=1, section_type="In-person",
                           days="Monday, Tuesday, Wednesday, and Thursday", time="12pm–12:50pm")
    assert sections_conflict(mth112, mth243)


# ── data-crn="and" continuation rows ──────────────────────────────────────────

def _lab_row(days_title: str, time_html: str) -> str:
    return _row("and", "In-person lab", days_title, time_html).replace(
        '<th scope="row">and</th>', '<th scope="row"><span class="visually-hidden">and</span></th>'
    )


def test_continuation_row_becomes_extra_meeting():
    html = _page(
        _row("40101", "In-person lecture", "Tuesday and Thursday", "from 9 - to 10:50am"),
        _lab_row("Thursday", "from 3 - to 5:50pm"),
        _row("40102", "In-person lecture", "Monday and Wednesday", "from 1 - to 2:50pm"),
    )
    sections = parse_detail_html(html, "G201", "Physical Geology")
    assert [s.crn for s in sections] == ["40101", "40102"]
    lecture_lab, lecture_only = sections
    assert (lecture_lab.days, lecture_lab.time) == ("Tuesday and Thursday", "9am–10:50am")
    assert lecture_lab.extra_meetings == [("Thursday", "3pm–5:50pm")]
    assert lecture_only.extra_meetings == []


def test_multiple_continuation_rows_attach_to_same_section():
    html = _page(
        _row("30601", "In-person lecture", "Monday and Wednesday", "from 9 - to 10:50am"),
        _lab_row("Friday", "from 9 - to 11:50am"),
        _lab_row("Tuesday", "from 11 - to 12:50pm"),
    )
    [sec] = parse_detail_html(html, "CH104", "Introductory Chemistry I")
    assert sec.extra_meetings == [("Friday", "9am–11:50am"), ("Tuesday", "11am–12:50pm")]


def test_continuation_row_without_meeting_is_ignored():
    html = _page(
        _row("30702", "Online", "", ""),
        _lab_row("", ""),
    )
    [sec] = parse_detail_html(html, "PHY121", "Elementary Physics")
    assert sec.extra_meetings == []


def test_extra_meetings_defaults_to_independent_empty_lists():
    a, b = CourseSection(), CourseSection()
    a.extra_meetings.append(("Friday", "1pm–3:50pm"))
    assert b.extra_meetings == []


def test_summary_includes_extra_meetings():
    sec = CourseSection(crn="30701", section_type="In-person", days="Monday and Wednesday",
                        time="10am–11:50am", seats=1,
                        extra_meetings=[("Friday", "1pm–3:50pm")])
    summary = sec.summary()
    assert "Monday and Wednesday 10am–11:50am" in summary
    assert "Friday 1pm–3:50pm" in summary


def test_summary_without_extra_meetings_unchanged():
    sec = CourseSection(crn="30701", section_type="In-person", days="Monday and Wednesday",
                        time="10am–11:50am", seats=1)
    assert sec.summary() == "CRN 30701 | In-person | Monday and Wednesday 10am–11:50am | Open"


# ── Section type normalisation ────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("Remote (scheduled online meetings)", "Remote"),
    ("Remote", "Remote"),
    ("remote lecture", "Remote"),
    ("Online (no scheduled meetings)", "Online"),
    ("Hybrid lecture", "Hybrid"),
    ("In-person lecture", "In-person"),
    ("Flexible", "Flexible"),
])
def test_section_type_normalised(raw, expected):
    html = _page(_row("30701", raw, "Monday and Wednesday", "from 10 - to 11:50am"))
    [sec] = parse_detail_html(html, "PHY121", "Elementary Physics")
    assert sec.section_type == expected
