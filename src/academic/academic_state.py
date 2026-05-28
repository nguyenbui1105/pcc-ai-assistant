"""Canonical academic state — single source of truth for all planning modules.

Computed ONCE per session from GRAD Plan audit + current enrollment.
Injected into every LLM call so the model never re-derives requirements.

Architecture contract:
  - LLM: reads to_llm_context() — may only EXPLAIN, never RECOMPUTE
  - Planner: uses completed_all and remaining_reqs
  - Recommender: uses completed_all, remaining_reqs, and profile hints
  - Redundancy engine: built from this state

No downstream module should independently infer degree requirements.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.mypcc.parser import DegreeAudit, DegreeRequirement
from src.schedule.gap_analyzer import AAOT_SUBJECT_MAP
from src.schedule.prereq_map import normalize_code

if TYPE_CHECKING:
    from src.mypcc.parser import CourseInfo


# AAOT lab science requirement rules (authoritative — prevents LLM hallucination)
_LAB_SCIENCE_RULE = (
    "3 lab science courses required. "
    "Courses must cover AT LEAST 2 different disciplines "
    "(e.g., PHY + BI counts; PHY + PHY + BI is fine). "
    "Disciplines: BI, CH, PHY, G, ES."
)

# Human-readable requirement descriptions for LLM context injection
_REQ_DESCRIPTIONS: dict[str, str] = {
    "Composition":          "1 WR course (WR121 or higher)",
    "Communication":        "1 COMM or SP course",
    "College Mathematics":  "1 MTH course (MTH111 or higher)",
    "Health and Wellness":  "PE/HE courses (≥3 credits total)",
    "Cultural Literacy":    "1 qualifying CL-designated course",
    "Arts and Letters":     "1 course from WR, ENG, HUM, PHL, ART, MUS, TA, COMM",
    "Social Sciences":      "2 courses from 2 DIFFERENT disciplines (SOC, PSY, GEO, HST, EC, PS, ATH, ANTH)",
    "Lab Requirement 1":    "1 lab science (BI, CH, PHY, G, or ES)",
    "Lab Requirement 2":    "1 lab science from a DIFFERENT discipline than Lab Req 1",
    "Lab Requirement 3":    f"1 more lab science. {_LAB_SCIENCE_RULE}",
    "Science/Math/Computer Science": "1 course from MTH, CS, BI, CH, or PHY",
}


@dataclass
class RequirementProgress:
    """Canonical status of a single degree requirement."""
    name: str
    status: str          # "complete" | "in_progress" | "incomplete"
    still_needed: str    # description from GRAD Plan
    description: str     # authoritative rule from _REQ_DESCRIPTIONS
    courses_done: list[str] = field(default_factory=list)   # normalized codes
    courses_ip: list[str] = field(default_factory=list)     # in-progress this term

    @property
    def is_complete(self) -> bool:
        return self.status == "complete"

    @property
    def is_incomplete(self) -> bool:
        return self.status in ("incomplete", "in_progress")

    def summary_line(self) -> str:
        icon = "✓" if self.is_complete else ("⏳" if self.status == "in_progress" else "✗")
        line = f"  {icon} {self.name}"
        if self.description:
            line += f": {self.description}"
        if self.still_needed and not self.is_complete:
            line += f" — Needed: {self.still_needed}"
        if self.courses_ip:
            line += f" (IP: {', '.join(self.courses_ip)})"
        return line


@dataclass
class AcademicState:
    """Single source of truth for all academic planning.

    Built from GRAD Plan audit + current enrollment.
    All planning modules consume this — none re-derive requirements.
    """
    degree: str
    gpa: str
    credits_applied: int
    credits_required: int
    audit_status: str    # "INCOMPLETE" | "COMPLETE" etc.

    # Canonical course sets (normalized codes, e.g. MTH65 not MTH065)
    completed: frozenset[str]       # officially passed
    in_progress: frozenset[str]     # enrolled this term
    completed_all: frozenset[str]   # completed | in_progress (for filtering)

    # Requirement statuses (GRAD Plan authoritative + IP augmentation)
    requirements: list[RequirementProgress]

    is_f1: bool = False

    # ── Derived properties ────────────────────────────────────────────────────

    @property
    def credits_remaining(self) -> int:
        return max(0, self.credits_required - self.credits_applied)

    @property
    def remaining_reqs(self) -> list[RequirementProgress]:
        return [r for r in self.requirements if not r.is_complete]

    @property
    def complete_req_names(self) -> frozenset[str]:
        return frozenset(r.name for r in self.requirements if r.is_complete)

    @property
    def incomplete_req_names(self) -> frozenset[str]:
        return frozenset(r.name for r in self.requirements if not r.is_complete)

    # ── LLM context injection ─────────────────────────────────────────────────

    def to_llm_context(self) -> str:
        """Return an authoritative degree state string for the LLM system prompt.

        The LLM must treat this as ground truth — it must NEVER recompute
        requirement status, course counts, or discipline rules.
        """
        if not self.degree:
            return ""

        lines = [
            "",
            "══════════════════════════════════════════════════",
            "STUDENT DEGREE STATE (AUTHORITATIVE — DO NOT RECOMPUTE)",
            "══════════════════════════════════════════════════",
            f"Degree: {self.degree}",
            f"GPA: {self.gpa}  |  Credits: {self.credits_applied}/{self.credits_required}"
            f"  ({self.credits_remaining} remaining to graduate)",
        ]
        if self.is_f1:
            lines.append("Status: F-1 International Student — MUST enroll ≥12 credits/term, ≥9 in-person")

        # Completed requirements
        done = [r for r in self.requirements if r.is_complete]
        if done:
            lines.append("")
            lines.append("COMPLETED REQUIREMENTS (do NOT recommend courses for these):")
            for r in done:
                courses_note = f" [{', '.join(r.courses_done[:3])}]" if r.courses_done else ""
                lines.append(f"  ✓ {r.name}{courses_note}")

        # Remaining requirements
        remaining = self.remaining_reqs
        if remaining:
            lines.append("")
            lines.append("REMAINING REQUIREMENTS (recommend courses ONLY for these):")
            for r in remaining:
                lines.append(r.summary_line())

        # In-progress courses this term
        if self.in_progress:
            lines.append("")
            lines.append(f"IN-PROGRESS THIS TERM (treat as if completed for planning):")
            lines.append(f"  {', '.join(sorted(self.in_progress))}")

        # Full completed list
        if self.completed:
            lines.append("")
            lines.append("COMPLETED COURSES (do not recommend these or lower-level versions):")
            # Show in sorted groups by subject
            by_subj: dict[str, list[str]] = {}
            for c in sorted(self.completed):
                m = re.match(r"^([A-Z]+)", c)
                subj = m.group(1) if m else "OTHER"
                by_subj.setdefault(subj, []).append(c)
            parts = [f"{s}: {', '.join(cs)}" for s, cs in sorted(by_subj.items())]
            lines.append("  " + " | ".join(parts))

        lines += [
            "",
            "RULE: Use the above as ground truth. Do NOT reinterpret transcript text.",
            "RULE: Do NOT state different discipline counts than shown above.",
            "RULE: Lab sciences need ≥2 disciplines (NOT ≥3) across 3 lab courses.",
            "══════════════════════════════════════════════════",
        ]
        return "\n".join(lines)

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_session(
        cls,
        audit: DegreeAudit,
        current_courses: list["CourseInfo"] | None = None,
        is_f1: bool = False,
    ) -> "AcademicState":
        """Build canonical AcademicState from GRAD Plan audit + current enrollment.

        Steps:
          1. Normalize all course codes
          2. Build in_progress from current_courses
          3. For each requirement, compute which courses contribute
          4. Augment GRAD Plan status with in-progress information
        """
        from src.schedule.gap_analyzer import AAOT_SUBJECT_MAP

        # 1. Normalize completed course codes
        completed_norm = frozenset(
            normalize_code(c) for c in (audit.completed_courses or [])
        )

        # 2. Build in-progress set from current term enrollment
        ip_codes: set[str] = set()
        if current_courses:
            from src.mypcc.parser import _course_id_to_code
            for course in current_courses:
                code = _course_id_to_code(course.course_id)
                if code:
                    ip_codes.add(normalize_code(code))

        # Remove in-progress from completed if they overlap (shouldn't, but safety)
        completed_norm = frozenset(c for c in completed_norm if c not in ip_codes)
        in_progress = frozenset(ip_codes)
        completed_all = completed_norm | in_progress

        # 3. Map completed + IP courses to their requirement subjects
        def _courses_in_subjects(codes: frozenset[str], subjects: list[str]) -> list[str]:
            return sorted(c for c in codes if any(c.startswith(s) for s in subjects))

        # 4. Build RequirementProgress for each requirement
        req_progresses: list[RequirementProgress] = []
        for req in (audit.requirements or []):
            subjects = AAOT_SUBJECT_MAP.get(req.name, [])
            courses_done = _courses_in_subjects(completed_norm, subjects)
            courses_ip = _courses_in_subjects(in_progress, subjects)
            description = _REQ_DESCRIPTIONS.get(req.name, "")

            # Augment status: if GRAD Plan says "incomplete" but we have IP courses
            # that contribute, mark as "in_progress" for better LLM context
            status = req.status
            if status == "incomplete" and courses_ip:
                status = "in_progress"

            req_progresses.append(RequirementProgress(
                name=req.name,
                status=status,
                still_needed=req.still_needed,
                description=description,
                courses_done=courses_done,
                courses_ip=courses_ip,
            ))

        return cls(
            degree=audit.degree,
            gpa=audit.gpa,
            credits_applied=audit.credits_applied,
            credits_required=audit.credits_required,
            audit_status=audit.status,
            completed=completed_norm,
            in_progress=in_progress,
            completed_all=completed_all,
            requirements=req_progresses,
            is_f1=is_f1,
        )
