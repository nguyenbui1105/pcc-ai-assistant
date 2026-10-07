"""Agent session: caches personal data loaded at chat start, tracks history."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.mypcc.parser import CourseInfo, DegreeAudit, DegreeRequirement, PersonalContext, ScheduledCourse

if TYPE_CHECKING:
    from src.academic.academic_state import AcademicState

_MAX_HISTORY = 20  # 10 turns


@dataclass
class AgentSession:
    personal_context: PersonalContext = field(default_factory=PersonalContext)
    degree_audit: DegreeAudit = field(default_factory=DegreeAudit)
    academic_state: "AcademicState | None" = field(default=None)
    schedule: list[ScheduledCourse] = field(default_factory=list)
    enrolled_times: dict[str, str] = field(default_factory=dict)  # CRN → "Tu/Th 9:30–11:50 AM"
    history: list[dict] = field(default_factory=list)
    is_international: bool = False
    student_name: str = ""
    is_demo: bool = False

    def add_turn(self, user: str, assistant: str) -> None:
        self.history.append({"role": "user", "content": user})
        self.history.append({"role": "assistant", "content": assistant[:800]})
        self.history = self.history[-_MAX_HISTORY:]

    @property
    def has_personal_data(self) -> bool:
        return self.personal_context.has_meaningful_data()

    @property
    def has_degree_audit(self) -> bool:
        return bool(self.degree_audit.degree)


def create_demo_session() -> AgentSession:
    """Realistic demo student for portfolio / public demo — no real PCC account needed."""
    from src.academic.academic_state import AcademicState
    from src.schedule.fetcher import TERM_LABELS, get_in_session_term

    # The term the demo student is enrolled in now (can be winter), not the planning default.
    current_term = TERM_LABELS[get_in_session_term()]

    audit = DegreeAudit(
        degree="Associate of Arts Oregon Transfer (AAOT)",
        gpa="3.40",
        credits_applied=45,
        credits_required=90,
        status="INCOMPLETE",
        requirements=[
            DegreeRequirement("Communication", "complete"),
            DegreeRequirement("Composition", "in-progress", still_needed="WR122"),
            DegreeRequirement("College Mathematics", "in-progress", still_needed="MTH111"),
            DegreeRequirement("Health and Wellness", "incomplete", still_needed="Any PE or HE course"),
            DegreeRequirement("Arts and Letters", "incomplete", still_needed="Any AL-coded course"),
            DegreeRequirement("Social Sciences", "complete"),
            DegreeRequirement("Lab Requirement 1", "complete"),
            DegreeRequirement("Lab Requirement 2", "incomplete", still_needed="CH or PHY course"),
            DegreeRequirement("Lab Requirement 3", "incomplete", still_needed="CH or PHY course"),
            DegreeRequirement("Cultural Literacy", "complete"),
        ],
        completed_courses=[
            "WR121", "COMM111", "COMM112",
            "SOC204", "SOC213",
            "MTH95", "CS161", "CS162",
            "BI101", "BI102",
            "ART115", "HE252",
        ],
    )

    ctx = PersonalContext(
        student_name="Alex",
        degree="Associate of Arts Oregon Transfer",
        advisor="Sarah Johnson",
        current_term=current_term,
        courses=[
            CourseInfo("Writing 122", "WR-122-12345", "Prof. Miller"),
            CourseInfo("College Algebra", "MTH-111-23456", "Prof. Chen"),
            CourseInfo("Web Development I", "CS-260-34567", "Prof. Park"),
        ],
        total_charges="$2,460.00",
        payments_received="$2,460.00",
        amount_due="$0.00",
        upcoming_dates=[
            f"{current_term} - Final exams week",
            "Next term - Registration opens (check MyPCC for your time ticket)",
        ],
        is_international=True,
    )

    # Add in-progress courses to completed so recommender filters them
    audit.completed_courses.extend(["WR122", "MTH111", "CS260"])

    return AgentSession(
        personal_context=ctx,
        degree_audit=audit,
        academic_state=AcademicState.from_session(audit, current_courses=ctx.courses, is_f1=True),
        is_international=True,
        student_name="Alex",
        is_demo=True,
    )
