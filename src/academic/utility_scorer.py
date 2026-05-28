"""Utility scoring engine: multi-factor ranking of courses and sections.

Score formula (all weights sum to 1.0):
  base_utility         × 0.25  — inherent strategic value of the course
  stem_alignment       × 0.20  — STEM relevance × student STEM affinity
  cs_alignment         × 0.15  — CS relevance × student CS orientation
  transfer_alignment   × 0.20  — transfer value × student transfer priority
  prereq_unlock        × 0.15  — future course unlock potential
  workload_safety      × 0.05  — bonus for high-GPA or penalty for weak students

Penalties (applied as multipliers):
  filler / support_lab → × 0.15  (heavily penalized)
  wellness (non-required) → × 0.35

The result is a float in [0.0, 1.0]. Higher = recommend sooner.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.academic.course_knowledge import CourseMetadata, get_course_metadata
from src.academic.student_model import StudentProfile
from src.schedule.parser import CourseSection


@dataclass
class ScoredSection:
    section: CourseSection
    score: float
    reasons: list[str] = field(default_factory=list)

    def __lt__(self, other: "ScoredSection") -> bool:
        return self.score < other.score


def score_course(
    course_code: str,
    profile: StudentProfile,
    gap_is_high_priority: bool = False,
    redundancy_penalty: float = 0.0,
) -> tuple[float, list[str]]:
    """Compute utility score for a course code given a student profile.

    Returns (score, reasons) where reasons explains the recommendation.
    """
    meta = get_course_metadata(course_code)
    reasons: list[str] = []

    # ── Penalties first — these dominate ────────────────────────────────────
    if redundancy_penalty >= 0.99:
        return 0.02, ["already completed or superseded"]

    if meta.is_filler:
        score = 0.12 * (1.0 - redundancy_penalty)
        return max(0.01, score), ["low strategic value"]

    if meta.is_wellness:
        score = 0.25 * (1.0 - redundancy_penalty)
        return max(0.02, score), ["wellness / PE course (satisfies graduation req)"]

    # ── Component scores ─────────────────────────────────────────────────────
    base = meta.base_utility

    # STEM alignment: how well does this match the student's STEM orientation?
    stem = meta.stem_relevance * profile.stem_affinity
    if stem >= 0.40 and meta.is_stem_lab:
        reasons.append("lab science — directly satisfies AAOT lab requirement")
    elif stem >= 0.30:
        reasons.append("STEM-aligned course")

    # CS alignment
    cs = meta.cs_relevance * (1.0 if profile.is_cs_oriented else 0.25)
    if cs >= 0.50:
        reasons.append("CS/computing pathway course")

    # Transfer alignment
    transfer = meta.transfer_value * profile.transfer_priority
    if transfer >= 0.60:
        target = profile.transfer_target or "Oregon 4-year universities"
        reasons.append(f"transferable to {target}")

    # Prereq unlock bonus
    unlock = meta.prereq_unlock
    if unlock >= 0.70:
        reasons.append("unlocks multiple upper-division courses")
    elif unlock >= 0.50:
        reasons.append("prerequisite for future coursework")

    # Workload safety
    # High-GPA students can handle high workload; lower scores for weak students
    workload_safety = 1.0 - meta.workload * (1.0 - profile.academic_strength)

    # High-priority requirement bonus
    req_boost = 0.10 if gap_is_high_priority else 0.0

    # ── Weighted combination ─────────────────────────────────────────────────
    raw = (
        0.25 * base
        + 0.20 * stem
        + 0.15 * cs
        + 0.20 * transfer
        + 0.15 * unlock
        + 0.05 * workload_safety
        + req_boost
    )
    # Apply redundancy penalty (0.0 = no penalty, 0.7 = 70% reduction)
    raw *= (1.0 - redundancy_penalty)
    score = max(0.0, min(1.0, raw))

    # Add tag-based reason if nothing else generated one
    if not reasons:
        if "transfer_core" in meta.tags:
            reasons.append("common transfer requirement")
        else:
            reasons.append("satisfies degree requirement")

    return score, reasons


def score_section(
    section: CourseSection,
    profile: StudentProfile,
    gap_is_high_priority: bool = False,
) -> ScoredSection:
    """Score a live CourseSection object."""
    score, reasons = score_course(
        section.course_code, profile, gap_is_high_priority
    )
    return ScoredSection(section=section, score=score, reasons=reasons)


def sort_sections_by_utility(
    sections: list[CourseSection],
    profile: StudentProfile,
    high_priority_codes: set[str] | None = None,
    checker=None,  # Optional[RedundancyChecker]
) -> list[CourseSection]:
    """Sort a flat list of sections from most to least strategically valuable.

    This is the key function called by the schedule planner before the
    greedy optimizer — ensures high-utility courses are picked first.
    Redundant/completed courses sink to the bottom of the list.
    """
    hp = high_priority_codes or set()
    scored = []
    for s in sections:
        rp = checker.redundancy_penalty(s.course_code) if checker else 0.0
        ss = ScoredSection(
            section=s,
            score=score_course(
                s.course_code,
                profile,
                gap_is_high_priority=(s.course_code in hp),
                redundancy_penalty=rp,
            )[0],
        )
        scored.append(ss)
    scored.sort(reverse=True)
    return [ss.section for ss in scored]


def rank_courses_for_display(
    course_codes: list[str],
    profile: StudentProfile,
    gap_label: str,
    gap_is_high_priority: bool = False,
) -> list[tuple[str, float, list[str]]]:
    """Rank and annotate a list of course codes for recommender display.

    Returns list of (course_code, score, reasons) sorted best-first.
    """
    results = []
    for code in course_codes:
        score, reasons = score_course(code, profile, gap_is_high_priority)
        # Add requirement context as first reason
        reasons = [f"satisfies {gap_label}"] + reasons
        results.append((code, score, reasons))
    results.sort(key=lambda x: -x[1])
    return results


def is_high_priority_gap(gap_label: str, profile: StudentProfile) -> bool:
    """True if this requirement should be treated as HIGH PRIORITY for this student.

    Lab sciences are high priority for STEM students.
    Core math is high priority for CS/STEM students.
    Wellness is low priority for everyone unless it's the only thing left.
    """
    label_lower = gap_label.lower()
    if profile.is_stem_oriented and any(
        kw in label_lower for kw in ["lab", "science", "math", "cs", "computing"]
    ):
        return True
    if profile.is_cs_oriented and "math" in label_lower:
        return True
    if any(kw in label_lower for kw in ["wellness", "pe", "health", "activity"]):
        return False
    return False
