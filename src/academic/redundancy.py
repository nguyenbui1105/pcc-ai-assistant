"""Redundancy detection engine for academic recommendations.

A course is redundant if any of the following is true:
  1. DIRECT: student already completed the course (exact or normalized match)
  2. SUPERSEDED: student has a higher-level course in the same subject
     (e.g., PSY202 done → PSY101 has no incremental value)
  3. SATURATED: the requirement this course satisfies is effectively complete
     based on completed courses (even if GRAD Plan status hasn't updated)

Usage:
  checker = RedundancyChecker.from_audit(audit)
  checker.is_redundant("PSY101")   # True if PSY202 done
  checker.redundancy_penalty("PSY101")  # 0.0-1.0 score
  checker.effective_gaps(gaps)     # filtered gap list
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from src.mypcc.parser import DegreeAudit
from src.schedule.gap_analyzer import AAOT_SUBJECT_MAP, SubjectGap
from src.schedule.prereq_map import get_superseded_courses, normalize_code

# Requirements satisfied by exactly 1 course (any qualifying course completes it)
_SINGLE_COURSE_REQS = frozenset({
    "Composition",
    "Communication",
    "College Mathematics",
    "Cultural Literacy",
    "Arts and Letters",
    "Health and Wellness",
})

# Requirements that need courses from N distinct subject disciplines
_MULTI_DISCIPLINE_REQS: dict[str, int] = {
    "Social Sciences": 2,    # need 2 different disciplines (SOC + PSY ≠ SOC + SOC)
    "Lab Requirement 1": 1,
    "Lab Requirement 2": 1,
    "Lab Requirement 3": 1,
}

# How many points above a completed course number makes a lower one "superseded"
_SUPERSEDE_GAP = 50


def _course_subject(code: str) -> str:
    m = re.match(r"^([A-Z]+)", code.upper())
    return m.group(1) if m else ""


def _course_number(code: str) -> int:
    m = re.search(r"\d+", code)
    return int(m.group()) if m else 0


@dataclass
class RedundancyChecker:
    """Pre-computed redundancy state for a single student's audit.

    Build once per planning call and reuse for all course checks.
    """
    # Normalized completed course codes (e.g. MTH65 not MTH065)
    completed_norm: frozenset[str] = field(default_factory=frozenset)

    # subject → sorted list of course numbers the student has completed
    # e.g. {"PSY": [101, 202], "WR": [121, 122]}
    subject_levels: dict[str, list[int]] = field(default_factory=dict)

    # requirement names that are effectively satisfied by completed courses
    saturated_reqs: frozenset[str] = field(default_factory=frozenset)

    # courses superseded by the prereq chain (e.g., MTH95 when MTH112 done)
    _prereq_superseded: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_state(cls, state: "AcademicState") -> "RedundancyChecker":  # type: ignore[name-defined]
        """Build from canonical AcademicState — preferred over from_audit."""
        # Temporarily import here to avoid circular imports at module level
        from src.academic.academic_state import AcademicState  # noqa: F401
        # Build a minimal audit-like structure from the state
        raw_completed = list(state.completed_all)
        completed_norm = frozenset(state.completed_all)

        subject_levels: dict[str, list[int]] = defaultdict(list)
        for code in completed_norm:
            subj = _course_subject(code)
            num = _course_number(code)
            if subj and num:
                subject_levels[subj].append(num)
        for levels in subject_levels.values():
            levels.sort()

        saturated = set(state.complete_req_names)

        for req_name, subjects in AAOT_SUBJECT_MAP.items():
            if req_name in saturated:
                continue
            courses_in_req = [c for c in completed_norm if _course_subject(c) in subjects]
            if not courses_in_req:
                continue
            if req_name in _SINGLE_COURSE_REQS:
                saturated.add(req_name)
            elif req_name in _MULTI_DISCIPLINE_REQS:
                distinct = {_course_subject(c) for c in courses_in_req}
                if len(distinct) >= _MULTI_DISCIPLINE_REQS[req_name]:
                    saturated.add(req_name)

        try:
            prereq_superseded = frozenset(
                normalize_code(c) for c in get_superseded_courses(raw_completed)
            )
        except Exception:
            prereq_superseded = frozenset()

        return cls(
            completed_norm=completed_norm,
            subject_levels=dict(subject_levels),
            saturated_reqs=frozenset(saturated),
            _prereq_superseded=prereq_superseded,
        )

    @classmethod
    def from_audit(cls, audit: DegreeAudit) -> "RedundancyChecker":
        """Build a checker from a degree audit."""
        raw_completed = list(audit.completed_courses or [])
        completed_norm = frozenset(normalize_code(c) for c in raw_completed)

        # Build subject → level map
        subject_levels: dict[str, list[int]] = defaultdict(list)
        for code in completed_norm:
            subj = _course_subject(code)
            num = _course_number(code)
            if subj and num:
                subject_levels[subj].append(num)
        for levels in subject_levels.values():
            levels.sort()

        # Determine effectively-saturated requirements
        saturated = set()

        # Also mark requirements whose status is already "complete" in the audit
        for req in (audit.requirements or []):
            if req.status == "complete":
                saturated.add(req.name)

        for req_name, subjects in AAOT_SUBJECT_MAP.items():
            if req_name in saturated:
                continue  # already marked complete

            # Collect completed courses that fall under this requirement's subjects
            courses_in_req = [
                c for c in completed_norm
                if _course_subject(c) in subjects
            ]
            if not courses_in_req:
                continue

            if req_name in _SINGLE_COURSE_REQS:
                # Any completion in the subject family satisfies it
                saturated.add(req_name)

            elif req_name in _MULTI_DISCIPLINE_REQS:
                required_disciplines = _MULTI_DISCIPLINE_REQS[req_name]
                distinct_disciplines = {_course_subject(c) for c in courses_in_req}
                if len(distinct_disciplines) >= required_disciplines:
                    saturated.add(req_name)

        # Build the prereq-chain superseded set using the static PREREQS graph
        # e.g., if MTH112 is done → get_superseded_courses returns MTH95, MTH65
        try:
            prereq_superseded = frozenset(
                normalize_code(c) for c in get_superseded_courses(raw_completed)
            )
        except Exception:
            prereq_superseded = frozenset()

        return cls(
            completed_norm=completed_norm,
            subject_levels=dict(subject_levels),
            saturated_reqs=frozenset(saturated),
            _prereq_superseded=prereq_superseded,
        )

    def is_completed(self, course_code: str) -> bool:
        """True if the student has exactly completed this course (normalized)."""
        norm = normalize_code(course_code)
        if norm in self.completed_norm:
            return True
        # Also check base code (strip trailing letter variant: WR121A → WR121)
        base = re.sub(r"[A-Z]$", "", norm)
        return base in self.completed_norm

    def is_superseded(self, course_code: str) -> bool:
        """True if this course is superseded by a completed higher-level course.

        Checks both:
        1. Prereq-chain supersession (MTH112 done → MTH95, MTH65 via PREREQS graph)
        2. Subject-level heuristic: any completed course 50+ levels above this one
           (PSY202 done → PSY101 superseded even without explicit prereq chain)
        """
        norm = normalize_code(course_code)

        # 1. Prereq-chain check (exact, based on static PREREQS graph)
        if norm in self._prereq_superseded:
            return True

        # 2. Subject-level proximity check
        subj = _course_subject(norm)
        num = _course_number(norm)
        if not subj or not num:
            return False
        levels = self.subject_levels.get(subj, [])
        return any(done > num + _SUPERSEDE_GAP for done in levels)

    def is_redundant(self, course_code: str) -> bool:
        """True if this course is directly completed or superseded."""
        return self.is_completed(course_code) or self.is_superseded(course_code)

    def is_requirement_saturated(self, req_name: str) -> bool:
        """True if this requirement is effectively complete based on completed courses."""
        return req_name in self.saturated_reqs

    def redundancy_penalty(self, course_code: str) -> float:
        """Return 0.0 (no penalty) to 1.0 (fully redundant).

        Used by the utility scorer to scale down the score of redundant courses.
        """
        if self.is_completed(course_code):
            return 1.0
        if self.is_superseded(course_code):
            return 0.85  # very high penalty but not 1.0 (might still be useful)

        # Softer penalty: same subject as a completed course but not superseded
        norm = normalize_code(course_code)
        subj = _course_subject(norm)
        num = _course_number(norm)
        if subj in self.subject_levels:
            max_done = max(self.subject_levels[subj])
            if max_done > num:
                # Student has higher-level course; lower one has less value
                # Penalty scales with the gap between levels
                gap = max_done - num
                return min(0.70, gap / 200.0)
        return 0.0

    def effective_gaps(self, gaps: list[SubjectGap]) -> list[SubjectGap]:
        """Filter out requirements that are effectively satisfied by completed courses.

        This is more conservative than analyze_gaps (which only uses GRAD Plan status).
        Removes requirements where completed courses already satisfy the need,
        preventing unnecessary recommendations of PSY101 when SOC204+GEO105 done.
        """
        return [g for g in gaps if not self.is_requirement_saturated(g.requirement)]

    def filter_redundant_subjects(
        self, gap: SubjectGap
    ) -> list[str]:
        """Return only the subjects in this gap that still need more courses.

        For Social Sciences: if student already has SOC (sociology), skip SOC
        and only recommend different-discipline subjects like PSY, EC, etc.
        """
        if gap.requirement not in _MULTI_DISCIPLINE_REQS:
            return gap.subjects

        needed_disciplines = _MULTI_DISCIPLINE_REQS[gap.requirement]
        all_in_req = [
            c for c in self.completed_norm
            if _course_subject(c) in gap.subjects
        ]
        covered_disciplines = {_course_subject(c) for c in all_in_req}

        if len(covered_disciplines) >= needed_disciplines:
            return []  # fully satisfied

        # Return subjects NOT already covered (different disciplines needed)
        return [s for s in gap.subjects if s not in covered_disciplines]
