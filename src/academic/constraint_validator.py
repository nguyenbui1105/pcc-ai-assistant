"""Hard and soft constraint validation for schedule plans.

Hard constraints MUST be satisfied before a plan is returned.
Soft constraints are preferences — violations generate warnings, not rejection.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from src.schedule.optimizer import SchedulePlan, SchedulePrefs, section_credits, sections_conflict


@dataclass
class PlanViolation:
    code: str        # machine-readable: UNDER_CREDITS, INSUFFICIENT_INPERSON, TIME_CONFLICT
    message: str     # human-readable explanation
    is_hard: bool    # hard=True → plan is invalid and must be repaired


def validate_plan(plan: SchedulePlan, prefs: SchedulePrefs) -> list[PlanViolation]:
    """Check a plan against all hard and soft constraints.

    Hard violations mean the plan cannot be presented to the student.
    Soft violations are noted but don't block presentation.
    """
    if not plan or not plan.sections:
        return [PlanViolation("EMPTY_PLAN", "No sections were selected.", is_hard=True)]

    violations: list[PlanViolation] = []
    total = plan.total_credits
    inperson_cr = sum(section_credits(s) for s in plan.sections if s.section_type != "Online")

    # ── HARD: F-1 minimum total credits ─────────────────────────────────────
    if prefs.is_international and total < 12:
        violations.append(PlanViolation(
            code="UNDER_CREDITS",
            message=f"F-1 requires ≥12 total credits; plan has {total}. Must add more courses.",
            is_hard=True,
        ))

    # ── HARD: F-1 minimum in-person credits ─────────────────────────────────
    if prefs.is_international and inperson_cr < 9:
        violations.append(PlanViolation(
            code="INSUFFICIENT_INPERSON",
            message=f"F-1 requires ≥9 in-person credits; plan has {inperson_cr}.",
            is_hard=True,
        ))

    # ── HARD: no time conflicts ──────────────────────────────────────────────
    for a, b in combinations(plan.sections, 2):
        if sections_conflict(a, b):
            violations.append(PlanViolation(
                code="TIME_CONFLICT",
                message=f"Time conflict between {a.course_code} and {b.course_code}.",
                is_hard=True,
            ))

    # ── HARD: general minimum credits (non-F-1) ──────────────────────────────
    if not prefs.is_international:
        min_cr = max(1, prefs.credit_target - 3)  # allow 3cr under target
        if total < min_cr:
            violations.append(PlanViolation(
                code="UNDER_CREDITS",
                message=f"Plan has only {total} credits (target {prefs.credit_target}).",
                is_hard=False,  # soft for non-F-1
            ))

    return violations


def has_hard_violations(plan: SchedulePlan, prefs: SchedulePrefs) -> bool:
    return any(v.is_hard for v in validate_plan(plan, prefs))


def format_violations(violations: list[PlanViolation]) -> str:
    hard = [v for v in violations if v.is_hard]
    soft = [v for v in violations if not v.is_hard]
    lines = []
    for v in hard:
        lines.append(f"⛔ **{v.code}**: {v.message}")
    for v in soft:
        lines.append(f"⚠️ {v.message}")
    return "\n".join(lines)
