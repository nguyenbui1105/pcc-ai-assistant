"""Regression tests for course-code normalization, prereq checks and GRAD Plan / MyPCC parsing."""
import pytest

from src.mypcc.parser import PersonalContext, _course_id_to_code, _parse_courses, parse_degree_audit
from src.schedule.prereq_map import (
    get_missing_prereqs,
    get_superseded_courses,
    has_prereqs_met,
    normalize_code,
)


# ── normalize_code: canonical comparison form ─────────────────────────────────

@pytest.mark.parametrize("code, expected", [
    ("MTH 243", "MTH243"),      # PCC's written form, with a space
    ("MTH 065", "MTH65"),
    (" mth 065 ", "MTH65"),
    ("PHL-201", "PHL201"),
    ("wr\t121", "WR121"),
    ("WR122Z", "WR122"),        # Oregon common course numbering
    ("WR 122Z", "WR122"),
    ("mth111z", "MTH111"),
    ("MTH 251Z", "MTH251"),
    ("PSY201A", "PSY201A"),     # other suffixes are kept
    ("CS161A", "CS161A"),
    ("G201", "G201"),
])
def test_normalize_code_canonical(code, expected):
    assert normalize_code(code) == expected


def test_spaced_codes_find_prereqs():
    assert get_missing_prereqs("WR 122", []) == ["WR121"]
    assert get_missing_prereqs("MTH 112", ["MTH 111"]) == []
    assert "MTH111" in get_superseded_courses(["MTH 112"])


def test_z_codes_share_the_unsuffixed_prereq_entry():
    assert get_missing_prereqs("MTH112Z", []) == ["MTH111"]
    assert get_missing_prereqs("MTH112", ["MTH111Z"]) == []
    assert get_missing_prereqs("WR 122Z", ["WR 121Z"]) == []
    assert {"WR121", "WR115"} <= get_superseded_courses(["WR122Z"])


# ── get_missing_prereqs: superseded prereqs count as met ─────────────────────

@pytest.mark.parametrize("course, completed", [
    ("ENG104", ["WR121", "WR122"]),   # WR121 supersedes WR115
    ("BI211", ["WR121"]),
    ("CH104", ["MTH95", "MTH111"]),   # MTH95 supersedes MTH65
    ("EC201", ["MTH111"]),
    ("WR122", ["WR123"]),             # WR123 needs WR121, so WR121 is covered
])
def test_prereq_met_by_superseding_course(course, completed):
    assert get_missing_prereqs(course, completed) == []
    assert has_prereqs_met(course, completed)


def test_unmet_prereq_still_reported():
    assert get_missing_prereqs("BI211", []) == ["WR115"]
    assert get_missing_prereqs("CH104", ["WR121"]) == ["MTH65"]
    assert not has_prereqs_met("CH104", ["WR121"])


def test_multi_entry_prereqs_are_all_required():
    # Entries are AND: each one must be met (directly or via a superseding course)
    assert get_missing_prereqs("CH221", ["MTH112"]) == ["CH104"]
    assert get_missing_prereqs("CH221", ["MTH251", "CH104"]) == []


# ── Calculus chain ────────────────────────────────────────────────────────────

def test_calculus_chain_prereqs():
    assert get_missing_prereqs("MTH252", ["MTH111"]) == ["MTH251"]
    assert get_missing_prereqs("MTH253", ["MTH112"]) == ["MTH252"]
    assert get_missing_prereqs("MTH253", ["MTH252"]) == []
    assert get_missing_prereqs("MTH252", ["MTH251Z"]) == []
    assert {"MTH252", "MTH251", "MTH112", "MTH111"} <= get_superseded_courses(["MTH253"])


# ── Single-letter subjects (G = Geology) ──────────────────────────────────────

def test_grad_plan_single_letter_subject():
    audit = parse_degree_audit(
        "Lab Requirement 1 Requirement is complete\n"
        "G 201 Physical Geology A- 4 Winter 2026\n"
    )
    assert audit.completed_courses == ["G201"]


def test_mypcc_course_id_single_letter_subject():
    assert _course_id_to_code("G-202-41236") == "G202"
    assert _course_id_to_code("WR-121-12345") == "WR121"


def test_parse_courses_single_letter_and_suffixed_ids():
    ctx = PersonalContext()
    _parse_courses(
        "Fall 2026\n"
        "Physical Geology\tG-202-41236\nJane Doe\n"
        "Computer Science I\tCS-161A-41300\nJohn Roe\n",
        ctx,
    )
    assert [(c.title, c.course_id, c.instructor) for c in ctx.courses] == [
        ("Physical Geology", "G-202-41236", "Jane Doe"),
        ("Computer Science I", "CS-161A-41300", "John Roe"),
    ]


# ── "Still needed" section ends at the next in-progress requirement ──────────

_HEADER = [
    "Associate of Arts Oregon Transfer INCOMPLETE",
    "Overall GPA 3.62",
    "Credits required: 90Credits applied: 58",
]


def test_in_progress_requirement_after_still_needed():
    text = "\n".join(_HEADER + [
        "Lab Requirement 2 Not complete",
        "Still needed:",
        "1 Class in BI 1@ or PHY 1@ or ES 1@",
        "Lab Requirement 3 Requirement will be complete when the in-progress classes are finished",
        "CH 104 Introductory Chemistry I IP (5) Fall 2026",
    ])
    audit = parse_degree_audit(text)
    assert audit.completed_courses == ["CH104"]
    assert ("Lab Requirement 3", "in-progress") in [(r.name, r.status) for r in audit.requirements]


@pytest.mark.parametrize("status", [
    "Requirement will be complete when the in-progress classes are finished",
    "When the in-progress classes are completed this requirement should be complete",
])
def test_graded_course_under_in_progress_requirement_kept(status):
    text = "\n".join(_HEADER + [
        "College Mathematics Not complete",
        "Still needed:",
        "1 Class in MTH 111 or 112 or 243 or 251",
        f"Health and Wellness {status}",
        "HE 250 Personal Health B+ 3 Spring 2026",
    ])
    # MTH 111 belongs to the Still-needed text and must stay excluded
    assert parse_degree_audit(text).completed_courses == ["HE250"]


def test_full_grad_plan_sample():
    text = "\n".join(_HEADER + [
        "Composition Requirement will be complete when the in-progress classes are finished",
        "WR 121 English Composition A 4 Fall 2025",
        "WR 122 English Composition: Argument IP (4) Fall 2026",
        "College Mathematics Not complete",
        "Still needed:",
        "1 Class in MTH 111 or 112 or 243 or 251",
        "Lab Requirement 1 Requirement is complete",
        "G 201 Physical Geology A- 4 Winter 2026",
        "Lab Requirement 2 Not complete",
        "Still needed:",
        "1 Class in BI 1@ or PHY 1@ or ES 1@",
        "Lab Requirement 3 Requirement will be complete when the in-progress classes are finished",
        "CH 104 Introductory Chemistry I IP (5) Fall 2026",
        "AAOT Credit Total Not complete",
        "Still needed:",
        "32 Credits",
        "Residency Requirement Requirement is complete",
    ])
    assert parse_degree_audit(text).completed_courses == ["WR121", "WR122", "G201", "CH104"]
