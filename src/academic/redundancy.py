"""Redundancy detection engine for academic recommendations.

A course is redundant if any of the following is true:
  1. DIRECT: student already completed the course (exact or normalized match)
  2. SUPERSEDED: student has a higher-level course in the same subject
     (e.g., PSY202 done → PSY101 has no incremental value)

Requirement status is NOT re-derived here: GRAD Plan is authoritative for which
completed courses count toward which requirement (analyze_gaps already drops
complete ones). Re-deriving it double-counts courses across requirements
(e.g. BI101 for Lab 1, Lab 2 and Lab 3).

Usage:
  checker = RedundancyChecker.from_audit(audit)
  checker.is_redundant("PSY101")   # True if PSY202 done
  checker.redundancy_penalty("PSY101")  # 0.0-1.0 score
  checker.filter_redundant_subjects(gap)  # prefer uncovered disciplines
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.mypcc.parser import DegreeAudit
from src.schedule.gap_analyzer import SubjectGap
from src.schedule.prereq_map import get_superseded_courses, normalize_code

if TYPE_CHECKING:
    from src.academic.academic_state import AcademicState

# Requirements that reward courses from disciplines the student hasn't covered yet
# (Social Sciences: SOC + PSY, not SOC + SOC; each lab block: a new lab science)
_MULTI_DISCIPLINE_REQS = frozenset({
    "Social Sciences",
    "Lab Requirement 1",
    "Lab Requirement 2",
    "Lab Requirement 3",
})

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

    # courses superseded by the prereq chain (e.g., MTH95 when MTH112 done)
    _prereq_superseded: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_state(cls, state: AcademicState) -> RedundancyChecker:
        """Build from canonical AcademicState — preferred over from_audit."""
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

        try:
            prereq_superseded = frozenset(
                normalize_code(c) for c in get_superseded_courses(raw_completed)
            )
        except Exception:
            prereq_superseded = frozenset()

        return cls(
            completed_norm=completed_norm,
            subject_levels=dict(subject_levels),
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

    def filter_redundant_subjects(
        self, gap: SubjectGap
    ) -> list[str]:
        """Return the subjects in this gap worth recommending, preferring new disciplines.

        For Social Sciences: if student already has SOC (sociology), skip SOC
        and only recommend different-discipline subjects like PSY, EC, etc.
        The gap is still open in GRAD Plan, so when every subject is already
        covered all of them are returned rather than none.
        """
        if gap.requirement not in _MULTI_DISCIPLINE_REQS:
            return gap.subjects

        covered_disciplines = {
            _course_subject(c) for c in self.completed_norm
            if _course_subject(c) in gap.subjects
        }
        uncovered = [s for s in gap.subjects if s not in covered_disciplines]
        return uncovered or gap.subjects
