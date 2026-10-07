"""Tests for src/academic/redundancy.py"""
import pytest

from src.academic.redundancy import RedundancyChecker
from src.agent.session import create_demo_session
from src.mypcc.parser import DegreeAudit, DegreeRequirement
from src.schedule.gap_analyzer import SubjectGap, analyze_gaps


def _audit(completed: list[str]) -> DegreeAudit:
    return DegreeAudit(
        degree="Associate of Arts Oregon Transfer (AAOT)",
        requirements=[DegreeRequirement("Lab Requirement 2", "incomplete")],
        completed_courses=completed,
    )


def _gap(requirement: str, subjects: list[str]) -> SubjectGap:
    return SubjectGap(requirement=requirement, label=requirement, subjects=subjects)


def test_completed_and_superseded_courses_are_redundant():
    checker = RedundancyChecker.from_audit(_audit(["PSY202", "MTH065"]))
    assert checker.is_redundant("PSY202")
    assert checker.is_redundant("MTH65")      # normalized match
    assert checker.is_redundant("PSY101")     # 101 levels below PSY202
    assert not checker.is_redundant("PSY201")


def test_lab_gap_prefers_new_discipline():
    checker = RedundancyChecker.from_audit(_audit(["BI101", "BI102"]))
    subjects = checker.filter_redundant_subjects(
        _gap("Lab Requirement 2", ["CH", "PHY", "G", "ES", "BI"])
    )
    assert subjects == ["CH", "PHY", "G", "ES"]


def test_open_gap_is_never_emptied_when_all_disciplines_covered():
    checker = RedundancyChecker.from_audit(_audit(["SOC204", "PSY201"]))
    gap = _gap("Social Sciences", ["SOC", "PSY"])
    assert checker.filter_redundant_subjects(gap) == ["SOC", "PSY"]


def test_single_discipline_gap_keeps_all_subjects():
    checker = RedundancyChecker.from_audit(_audit(["ART115"]))
    gap = _gap("Arts and Letters", ["WR", "ART", "MUS"])
    assert checker.filter_redundant_subjects(gap) == ["WR", "ART", "MUS"]


@pytest.mark.parametrize("use_state", [False, True])
def test_demo_student_open_requirements_all_get_subjects(use_state):
    """Regression: BI101/BI102 (Lab 1) and HE252 must not close Lab 2/3 or Health."""
    session = create_demo_session()
    checker = (
        RedundancyChecker.from_state(session.academic_state)
        if use_state else RedundancyChecker.from_audit(session.degree_audit)
    )
    gaps = analyze_gaps(session.degree_audit)
    assert {g.requirement for g in gaps} == {
        "Composition", "College Mathematics", "Health and Wellness",
        "Arts and Letters", "Lab Requirement 2", "Lab Requirement 3",
    }
    for gap in gaps:
        assert checker.filter_redundant_subjects(gap), gap.requirement
