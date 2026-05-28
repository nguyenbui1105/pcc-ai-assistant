"""Tests for src/schedule/gap_analyzer.py"""
import pytest
from src.schedule.gap_analyzer import (
    analyze_gaps,
    course_to_requirement,
    get_unique_subjects,
    SubjectGap,
)
from src.mypcc.parser import DegreeAudit, DegreeRequirement


def _audit(*reqs: tuple[str, str]) -> DegreeAudit:
    return DegreeAudit(
        degree="AAOT",
        requirements=[DegreeRequirement(name=r, status=s) for r, s in reqs],
    )


# ── analyze_gaps ──────────────────────────────────────────────────────────────

def test_analyze_gaps_incomplete():
    audit = _audit(
        ("Composition", "incomplete"),
        ("Social Sciences", "complete"),
    )
    gaps = analyze_gaps(audit)
    assert len(gaps) == 1
    assert gaps[0].requirement == "Composition"
    assert "WR" in gaps[0].subjects

def test_analyze_gaps_all_complete():
    audit = _audit(
        ("Composition", "complete"),
        ("Social Sciences", "complete"),
    )
    gaps = analyze_gaps(audit)
    assert gaps == []

def test_analyze_gaps_unknown_requirement():
    audit = _audit(("Unknown Req", "incomplete"))
    gaps = analyze_gaps(audit)
    assert gaps == []  # not in AAOT_SUBJECT_MAP → skipped

def test_analyze_gaps_multiple():
    audit = _audit(
        ("Composition", "incomplete"),
        ("Lab Requirement 1", "incomplete"),
        ("Social Sciences", "incomplete"),
    )
    gaps = analyze_gaps(audit)
    assert len(gaps) == 3


# ── course_to_requirement ─────────────────────────────────────────────────────

def _gaps_for(*req_names: str) -> list[SubjectGap]:
    audit = _audit(*[(r, "incomplete") for r in req_names])
    return analyze_gaps(audit)

def test_course_to_requirement_wr():
    gaps = _gaps_for("Composition")
    assert course_to_requirement("WR121", gaps) == "Composition (writing)"

def test_course_to_requirement_soc():
    gaps = _gaps_for("Social Sciences")
    assert course_to_requirement("SOC204", gaps) == "Social Sciences"

def test_course_to_requirement_bi():
    gaps = _gaps_for("Lab Requirement 1")
    assert course_to_requirement("BI101", gaps) == "Lab Science 1 (BI, CH, PHY, or G with lab)"

def test_course_to_requirement_no_match():
    gaps = _gaps_for("Composition")
    assert course_to_requirement("CS160", gaps) == ""  # CS not in Composition subjects

def test_course_to_requirement_wr_arts_letters():
    # WR appears in Arts and Letters too; should match whichever gap is active
    gaps = _gaps_for("Arts and Letters")
    result = course_to_requirement("WR227", gaps)
    assert result == "Arts & Letters"

def test_course_to_requirement_prefers_active_gap():
    # WR maps to both Composition AND Arts and Letters; return the one that's a gap
    gaps = _gaps_for("Arts and Letters")  # Composition is complete
    result = course_to_requirement("WR121", gaps)
    assert result == "Arts & Letters"


# ── get_unique_subjects ───────────────────────────────────────────────────────

def test_get_unique_subjects_no_dupes():
    # WR appears in Composition AND Arts and Letters — should appear once
    gaps = _gaps_for("Composition", "Arts and Letters")
    subjects = get_unique_subjects(gaps)
    assert subjects.count("WR") == 1

def test_get_unique_subjects_order():
    gaps = _gaps_for("Composition")
    subjects = get_unique_subjects(gaps)
    assert subjects[0] == "WR"
