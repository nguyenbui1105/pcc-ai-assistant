"""Tests for src/academic/academic_state.py and the demo session built on it."""
from src.academic.academic_state import AcademicState
from src.agent.session import create_demo_session
from src.mypcc.parser import CourseInfo, DegreeAudit, DegreeRequirement
from src.schedule.fetcher import TERM_LABELS, get_in_session_term


def _audit(*reqs: DegreeRequirement, completed: list[str] | None = None) -> DegreeAudit:
    return DegreeAudit(
        degree="Associate of Arts Oregon Transfer (AAOT)",
        gpa="3.50",
        credits_applied=45,
        credits_required=90,
        status="INCOMPLETE",
        requirements=list(reqs),
        completed_courses=completed or [],
    )


def test_grad_plan_in_progress_status_is_normalized():
    state = AcademicState.from_session(
        _audit(DegreeRequirement("Composition", "in-progress", still_needed="WR122"))
    )
    req = state.requirements[0]
    assert req.status == "in_progress"
    assert req.is_incomplete
    assert "⏳ Composition" in req.summary_line()


def test_incomplete_with_enrolled_course_stays_incomplete():
    # GRAD Plan status is authoritative: an enrolled course in the requirement's
    # subject doesn't upgrade it, it is only listed as in progress this term
    state = AcademicState.from_session(
        _audit(DegreeRequirement("College Mathematics", "incomplete")),
        current_courses=[CourseInfo("College Algebra", "MTH-111-23456", "Prof. Chen")],
    )
    assert state.requirements[0].status == "incomplete"
    assert state.requirements[0].courses_ip == []
    assert state.in_progress == frozenset({"MTH111"})
    assert "MTH111" in state.to_llm_context()


def test_completed_all_includes_in_progress_and_normalizes_codes():
    state = AcademicState.from_session(
        _audit(completed=["MTH065", "WR121"]),
        current_courses=[CourseInfo("Writing 122", "WR-122-12345", "Prof. Miller")],
    )
    assert state.completed == frozenset({"MTH65", "WR121"})
    assert state.completed_all == frozenset({"MTH65", "WR121", "WR122"})


def test_demo_session_has_academic_state_and_current_term():
    session = create_demo_session()
    assert session.is_demo
    assert session.personal_context.current_term == TERM_LABELS[get_in_session_term()]
    state = session.academic_state
    assert state is not None
    assert state.is_f1
    assert state.in_progress == frozenset({"WR122", "MTH111", "CS260"})
    assert "STUDENT DEGREE STATE" in state.to_llm_context()
