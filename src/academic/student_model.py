"""Structured student profile inferred from degree audit and session data.

The profile drives utility scoring — a CS/transfer student gets very different
recommendations than a general-education or wellness-focused student.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.mypcc.parser import DegreeAudit

# Subjects that indicate STEM orientation
_STEM_SUBJECTS = frozenset(
    ["CS", "MTH", "PHY", "CH", "BI", "G", "ES", "CIS", "EET", "MET", "ET"]
)

# Subjects associated with CS / computing specifically
_CS_SUBJECTS = frozenset(["CS", "CIS", "MTH"])

# Transfer target keywords → canonical name
_TRANSFER_ALIASES: dict[str, str] = {
    "psu": "PSU", "portland state": "PSU",
    "osu": "OSU", "oregon state": "OSU",
    "uo": "UO", "university of oregon": "UO", "oregon": "UO",
    "wsu": "WSU", "washington state": "WSU",
    "linfield": "Linfield", "willamette": "Willamette",
}


@dataclass
class StudentProfile:
    """Structured representation of a student's academic context and goals."""

    # Identity / goals
    major_interest: str = "General"    # "CS/STEM", "Natural Sciences", "Nursing",
                                        # "Business", "Liberal Arts", "General"
    transfer_target: str = ""          # "PSU", "OSU", "UO", ""

    # Derived numeric signals (0.0 – 1.0)
    academic_strength: float = 0.7     # from GPA: 4.0 → 1.0, 2.0 → 0.5
    stem_affinity: float = 0.5         # fraction of completed credits in STEM subjects
    cs_affinity: float = 0.0           # fraction in CS-specific subjects
    transfer_priority: float = 0.5     # how strongly to weight transfer value

    # Hard constraints
    is_f1: bool = False
    min_credits: int = 9

    # Derived from degree progress
    credits_remaining: int = 0         # credits still needed

    @property
    def is_stem_oriented(self) -> bool:
        return self.stem_affinity >= 0.3 or self.major_interest == "CS/STEM"

    @property
    def is_cs_oriented(self) -> bool:
        return self.cs_affinity >= 0.2 or self.major_interest == "CS/STEM"

    @property
    def is_transfer_student(self) -> bool:
        return bool(self.transfer_target) or self.transfer_priority >= 0.6


def infer_profile(audit: DegreeAudit, is_f1: bool = False) -> StudentProfile:
    """Infer a StudentProfile from the degree audit + session flags.

    All fields are heuristic estimates — they can be overridden later if the
    student explicitly states their goals in conversation.
    """
    profile = StudentProfile(is_f1=is_f1)

    if is_f1:
        profile.min_credits = 12
        profile.transfer_priority = 0.7   # F-1 students are usually planning to transfer

    # Academic strength from GPA
    try:
        gpa = float(audit.gpa or "0")
        profile.academic_strength = min(1.0, max(0.0, (gpa - 1.5) / 2.5))
    except ValueError:
        profile.academic_strength = 0.65

    # Credits remaining
    try:
        remaining = int(audit.credits_required or 0) - int(audit.credits_applied or 0)
        profile.credits_remaining = max(0, remaining)
    except (ValueError, TypeError):
        profile.credits_remaining = 45

    # STEM / CS affinity from completed courses
    completed = [c.upper() for c in (audit.completed_courses or [])]
    stem_count = sum(1 for c in completed if _course_subject(c) in _STEM_SUBJECTS)
    cs_count   = sum(1 for c in completed if _course_subject(c) in _CS_SUBJECTS)

    if completed:
        profile.stem_affinity = min(1.0, stem_count / len(completed))
        profile.cs_affinity   = min(1.0, cs_count   / len(completed))

    # Major interest inference
    if cs_count >= 2 or (stem_count >= 3 and cs_count >= 1):
        profile.major_interest = "CS/STEM"
    elif stem_count >= 3:
        bio_chem = sum(
            1 for c in completed
            if _course_subject(c) in {"BI", "CH", "PHY", "G", "ES"}
        )
        profile.major_interest = "Natural Sciences" if bio_chem >= 2 else "CS/STEM"
    elif any(_course_subject(c) in {"SOC", "PSY", "HST", "ANTH", "EC", "PS"} for c in completed):
        profile.major_interest = "Liberal Arts"
    else:
        profile.major_interest = "General"

    # Transfer priority: high if F-1, high if STEM (STEM students almost always transfer)
    if profile.is_stem_oriented:
        profile.transfer_priority = max(profile.transfer_priority, 0.8)

    return profile


def enrich_profile_from_text(profile: StudentProfile, text: str) -> StudentProfile:
    """Update a profile with hints extracted from a user message or preferences string.

    Examples:
      "I'm planning to transfer to PSU" → transfer_target="PSU", transfer_priority=0.9
      "I'm interested in CS/AI"         → major_interest="CS/STEM"
      "nursing program"                 → major_interest="Nursing"
    """
    text_lower = text.lower()

    # Transfer target
    for alias, canonical in _TRANSFER_ALIASES.items():
        if alias in text_lower:
            profile.transfer_target = canonical
            profile.transfer_priority = 0.90
            break

    # Major interest
    if any(kw in text_lower for kw in ["cs", "computer", "software", "ai", "ml", "data science", "programming"]):
        profile.major_interest = "CS/STEM"
        profile.cs_affinity = max(profile.cs_affinity, 0.5)
        profile.stem_affinity = max(profile.stem_affinity, 0.5)
    elif any(kw in text_lower for kw in ["nursing", "health", "pre-med", "medicine", "pharmacy"]):
        profile.major_interest = "Nursing"
        profile.stem_affinity = max(profile.stem_affinity, 0.4)
    elif any(kw in text_lower for kw in ["business", "accounting", "finance", "marketing"]):
        profile.major_interest = "Business"
    elif any(kw in text_lower for kw in ["biology", "chemistry", "physics", "science"]):
        profile.major_interest = "Natural Sciences"
        profile.stem_affinity = max(profile.stem_affinity, 0.6)

    return profile


def _course_subject(code: str) -> str:
    """Extract subject prefix from course code, e.g. 'CS161A' → 'CS'."""
    m = re.match(r"^([A-Z]+)", code.upper())
    return m.group(1) if m else ""
