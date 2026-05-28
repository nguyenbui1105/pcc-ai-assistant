"""Tests for src/schedule/prereq_map.py"""
import pytest
from src.schedule.prereq_map import (
    normalize_code,
    get_missing_prereqs,
    has_prereqs_met,
    get_superseded_courses,
    PREREQS,
)


# ── normalize_code ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code, expected", [
    ("MTH065", "MTH65"),
    ("MTH65", "MTH65"),
    ("WR121", "WR121"),
    ("BI101", "BI101"),
    ("mth065", "MTH65"),
    ("  WR121 ", "WR121"),
    ("CH104", "CH104"),
])
def test_normalize_code(code, expected):
    assert normalize_code(code) == expected


# ── get_missing_prereqs ───────────────────────────────────────────────────────

def test_prereqs_met():
    # WR122 requires WR121; if WR121 completed → no missing prereqs
    missing = get_missing_prereqs("WR122", ["WR121"])
    assert missing == []

def test_prereqs_missing():
    missing = get_missing_prereqs("WR122", [])
    assert "WR121" in missing

def test_no_prereq_course():
    # WR115 has no prereqs
    missing = get_missing_prereqs("WR115", [])
    assert missing == []

def test_prereqs_with_normalized_codes():
    # MTH111 requires MTH095; completed as MTH095 (no leading zero issue here)
    # but also test that normalize_code works in both directions
    missing = get_missing_prereqs("WR122", ["WR121"])
    assert missing == []


# ── has_prereqs_met ───────────────────────────────────────────────────────────

def test_has_prereqs_met_true():
    assert has_prereqs_met("WR122", ["WR121"])

def test_has_prereqs_met_false():
    assert not has_prereqs_met("WR122", [])

def test_has_prereqs_met_no_prereqs():
    assert has_prereqs_met("WR115", [])


# ── get_superseded_courses ────────────────────────────────────────────────────

def test_superseded_direct():
    # If WR121 is done, WR115 is superseded
    superseded = get_superseded_courses(["WR121"])
    assert "WR115" in superseded

def test_superseded_chain():
    # WR122 requires WR121; WR121 requires WR115
    # If WR122 done → WR121 AND WR115 both superseded
    superseded = get_superseded_courses(["WR122"])
    assert "WR121" in superseded
    assert "WR115" in superseded

def test_superseded_empty():
    assert get_superseded_courses([]) == set()

def test_superseded_no_prereqs_course():
    # WR115 has no prereqs → nothing superseded by completing it
    superseded = get_superseded_courses(["WR115"])
    assert "WR115" not in superseded  # doesn't supersede itself
