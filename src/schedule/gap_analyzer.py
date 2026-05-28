"""Map DegreeAudit incomplete requirements to PCC subject codes."""
from dataclasses import dataclass, field

from src.mypcc.parser import DegreeAudit, DegreeRequirement

# Maps tracked requirement names (from parser.py) to PCC subject codes.
# Order within each list = recommendation priority.
AAOT_SUBJECT_MAP: dict[str, list[str]] = {
    "Composition":           ["WR"],
    "Communication":         ["COMM", "SP"],
    "Health and Wellness":   ["PE", "HE"],
    "College Mathematics":   ["MTH"],
    "Cultural Literacy":     ["SOC", "ANTH", "HUM", "WS"],
    "Arts and Letters":      ["WR", "COMM", "ENG", "HUM", "PHL", "ART", "MUS", "TA"],
    "Social Sciences":       ["SOC", "PSY", "GEO", "ATH", "EC", "PS", "HST", "ANTH"],
    # Each lab block leads with a different discipline to encourage variety
    "Lab Requirement 1":     ["BI", "CH", "PHY", "G", "ES"],
    "Lab Requirement 2":     ["CH", "PHY", "G", "ES", "BI"],
    "Lab Requirement 3":     ["PHY", "G", "ES", "BI", "CH"],
    "Science/Math/Computer Science": ["MTH", "CS", "BI", "CH", "PHY"],
}

# Human-readable labels for requirements
REQ_LABELS: dict[str, str] = {
    "Composition":           "Composition (writing)",
    "Communication":         "Communication",
    "Health and Wellness":   "Health & Wellness",
    "College Mathematics":   "College Mathematics",
    "Cultural Literacy":     "Cultural Literacy",
    "Arts and Letters":      "Arts & Letters",
    "Social Sciences":       "Social Sciences",
    "Lab Requirement 1":     "Lab Science 1 (BI, CH, PHY, or G with lab)",
    "Lab Requirement 2":     "Lab Science 2 — different discipline from Lab 1",
    "Lab Requirement 3":     "Lab Science 3 — must cover ≥2 total disciplines",
    "Science/Math/Computer Science": "Science / Math / CS",
}


def course_to_requirement(course_code: str, gaps: list["SubjectGap"]) -> str:
    """Return the unfulfilled requirement label a course helps satisfy, or ''.

    Checks each active gap's subject list directly so the same subject (e.g. WR)
    can match whichever requirement is actually still incomplete for this student.
    """
    prefix = "".join(c for c in course_code if c.isalpha()).upper()
    # Try longest prefix first so "COMM" matches before "CO"
    for length in range(len(prefix), 0, -1):
        subj = prefix[:length]
        for gap in gaps:
            if subj in gap.subjects:
                return gap.label
    return ""


@dataclass
class SubjectGap:
    requirement: str          # e.g. "Arts and Letters"
    label: str                # human-readable
    subjects: list[str]       # e.g. ["WR", "COMM", "ENG", ...]
    still_needed: str = ""    # free-text from GRAD Plan


def analyze_gaps(audit: DegreeAudit) -> list[SubjectGap]:
    """Return SubjectGap objects for each incomplete/in-progress requirement."""
    gaps: list[SubjectGap] = []
    for req in audit.requirements:
        if req.status == "complete":
            continue
        subjects = AAOT_SUBJECT_MAP.get(req.name)
        if not subjects:
            continue
        gaps.append(SubjectGap(
            requirement=req.name,
            label=REQ_LABELS.get(req.name, req.name),
            subjects=subjects,
            still_needed=req.still_needed,
        ))
    return gaps


def get_unique_subjects(gaps: list[SubjectGap]) -> list[str]:
    """Deduplicated, ordered list of subject codes across all gaps."""
    seen: set[str] = set()
    out: list[str] = []
    for gap in gaps:
        for subj in gap.subjects:
            if subj not in seen:
                seen.add(subj)
                out.append(subj)
    return out
