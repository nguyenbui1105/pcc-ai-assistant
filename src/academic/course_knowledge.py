"""Course knowledge base: strategic metadata and utility scoring for PCC courses.

Architecture:
  1. COURSE_KB  — curated entries for ~100 high-impact courses
  2. _SUBJECT_DEFAULTS — subject-level heuristics for uncurated courses
  3. _detect_support_lab() — pattern rules for support labs, PE variants, etc.
  4. get_course_metadata() — lookup with multi-level fallback

Tags (used for filtering and scoring):
  stem         — STEM subject area
  stem_lab     — satisfies AAOT lab science requirement
  cs_pathway   — important for CS / computing career track
  transfer_core— required or strongly preferred at Oregon 4-year universities
  foundational — prerequisite-heavy; unlocks many future courses
  writing      — composition / writing course
  communication— speech / communication
  social_sci   — social science
  arts_letters — arts & letters
  wellness     — PE / HE courses
  filler       — low strategic value relative to AAOT requirements
  support_lab  — supplemental lab / support section (WR115, WRnnL, PEnnX)
  high_workload— 5-credit or very demanding course
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import FrozenSet


@dataclass(frozen=True)
class CourseMetadata:
    """Static strategic metadata for a PCC course."""
    tags: FrozenSet[str]

    # 0.0 – 1.0 scores
    transfer_value: float   # How strongly this transfers to Oregon 4-year schools
    stem_relevance: float   # STEM subject importance
    cs_relevance: float     # CS / computing relevance
    prereq_unlock: float    # Fraction of future courses this directly enables
    workload: float         # Relative demand (1.0 = very heavy)
    base_utility: float     # Overall strategic baseline (regardless of student profile)

    @property
    def is_filler(self) -> bool:
        return bool({"filler", "support_lab"} & self.tags)

    @property
    def is_wellness(self) -> bool:
        return "wellness" in self.tags

    @property
    def is_stem_lab(self) -> bool:
        return "stem_lab" in self.tags

    @property
    def is_cs(self) -> bool:
        return "cs_pathway" in self.tags


def _meta(
    tags: set[str],
    transfer: float,
    stem: float,
    cs: float = 0.0,
    unlock: float = 0.2,
    workload: float = 0.5,
    base: float | None = None,
) -> CourseMetadata:
    _base = base if base is not None else (
        0.4 * transfer + 0.3 * stem + 0.2 * cs + 0.1 * unlock
    )
    return CourseMetadata(
        tags=frozenset(tags),
        transfer_value=transfer,
        stem_relevance=stem,
        cs_relevance=cs,
        prereq_unlock=unlock,
        workload=workload,
        base_utility=min(1.0, _base),
    )


# ── Curated course knowledge base ────────────────────────────────────────────
#
# Covers ~100 key PCC courses that students encounter most often.
# Unlisted courses fall back to subject-level heuristics in get_course_metadata().

COURSE_KB: dict[str, CourseMetadata] = {

    # ── Physics (high-value lab science + CS/engineering pathway) ────────────
    "PHY121": _meta({"stem","stem_lab","cs_pathway","transfer_core"}, transfer=0.90, stem=0.95, cs=0.70, unlock=0.65, workload=0.65),
    "PHY122": _meta({"stem","stem_lab","cs_pathway","transfer_core"}, transfer=0.90, stem=0.95, cs=0.70, unlock=0.60, workload=0.65),
    "PHY123": _meta({"stem","stem_lab","cs_pathway","transfer_core"}, transfer=0.90, stem=0.95, cs=0.70, unlock=0.55, workload=0.65),
    "PHY100": _meta({"stem","stem_lab","transfer_core"},              transfer=0.75, stem=0.75, cs=0.20, unlock=0.20, workload=0.40),
    "PHY201": _meta({"stem","stem_lab","cs_pathway","transfer_core","high_workload"}, transfer=0.93, stem=0.98, cs=0.85, unlock=0.70, workload=0.85),
    "PHY202": _meta({"stem","stem_lab","cs_pathway","transfer_core","high_workload"}, transfer=0.93, stem=0.98, cs=0.85, unlock=0.65, workload=0.85),
    "PHY203": _meta({"stem","stem_lab","cs_pathway","transfer_core","high_workload"}, transfer=0.93, stem=0.98, cs=0.85, unlock=0.55, workload=0.85),

    # ── Chemistry ────────────────────────────────────────────────────────────
    "CH100": _meta({"stem","stem_lab"},                              transfer=0.65, stem=0.70, cs=0.10, unlock=0.30, workload=0.50),
    "CH104": _meta({"stem","stem_lab","transfer_core","high_workload"}, transfer=0.85, stem=0.90, cs=0.20, unlock=0.70, workload=0.75),
    "CH105": _meta({"stem","stem_lab","transfer_core","high_workload"}, transfer=0.85, stem=0.90, cs=0.20, unlock=0.60, workload=0.75),
    "CH106": _meta({"stem","stem_lab","transfer_core","high_workload"}, transfer=0.85, stem=0.90, cs=0.20, unlock=0.50, workload=0.75),
    "CH221": _meta({"stem","stem_lab","foundational","transfer_core","high_workload"}, transfer=0.92, stem=0.95, cs=0.15, unlock=0.85, workload=0.90),
    "CH222": _meta({"stem","stem_lab","transfer_core","high_workload"}, transfer=0.92, stem=0.95, cs=0.15, unlock=0.75, workload=0.90),
    "CH223": _meta({"stem","stem_lab","transfer_core","high_workload"}, transfer=0.92, stem=0.95, cs=0.15, unlock=0.65, workload=0.90),

    # ── Biology ──────────────────────────────────────────────────────────────
    "BI101": _meta({"stem","stem_lab","transfer_core"},              transfer=0.80, stem=0.82, cs=0.10, unlock=0.40, workload=0.55),
    "BI102": _meta({"stem","stem_lab","transfer_core"},              transfer=0.80, stem=0.82, cs=0.10, unlock=0.40, workload=0.55),
    "BI111": _meta({"stem","stem_lab","foundational","transfer_core"}, transfer=0.85, stem=0.87, cs=0.10, unlock=0.75, workload=0.60),
    "BI112": _meta({"stem","stem_lab","transfer_core"},              transfer=0.85, stem=0.87, cs=0.10, unlock=0.65, workload=0.60),
    "BI120": _meta({"stem","stem_lab","transfer_core"},              transfer=0.78, stem=0.80, cs=0.10, unlock=0.30, workload=0.50),
    "BI164": _meta({"stem","stem_lab","transfer_core"},              transfer=0.78, stem=0.80, cs=0.10, unlock=0.30, workload=0.50),
    "BI211": _meta({"stem","stem_lab","foundational","transfer_core","high_workload"}, transfer=0.90, stem=0.92, cs=0.10, unlock=0.85, workload=0.80),
    "BI212": _meta({"stem","stem_lab","transfer_core","high_workload"}, transfer=0.90, stem=0.92, cs=0.10, unlock=0.75, workload=0.80),
    "BI213": _meta({"stem","stem_lab","transfer_core","high_workload"}, transfer=0.90, stem=0.92, cs=0.10, unlock=0.65, workload=0.80),
    "BI231": _meta({"stem","stem_lab","transfer_core","high_workload"}, transfer=0.88, stem=0.90, cs=0.10, unlock=0.60, workload=0.80),
    "BI232": _meta({"stem","stem_lab","transfer_core","high_workload"}, transfer=0.88, stem=0.90, cs=0.10, unlock=0.55, workload=0.80),
    "BI233": _meta({"stem","stem_lab","transfer_core","high_workload"}, transfer=0.88, stem=0.90, cs=0.10, unlock=0.50, workload=0.80),

    # ── Geology / Earth Science ───────────────────────────────────────────────
    "G201":  _meta({"stem","stem_lab","transfer_core"},              transfer=0.78, stem=0.78, cs=0.10, unlock=0.25, workload=0.55),
    "G202":  _meta({"stem","stem_lab","transfer_core"},              transfer=0.78, stem=0.78, cs=0.10, unlock=0.20, workload=0.55),
    "G203":  _meta({"stem","stem_lab","transfer_core"},              transfer=0.78, stem=0.78, cs=0.10, unlock=0.20, workload=0.55),
    "ES101": _meta({"stem","stem_lab","transfer_core"},              transfer=0.72, stem=0.72, cs=0.10, unlock=0.20, workload=0.50),
    "ES252": _meta({"stem","stem_lab","transfer_core"},              transfer=0.70, stem=0.70, cs=0.10, unlock=0.15, workload=0.50),

    # ── Computer Science ──────────────────────────────────────────────────────
    "CS120":  _meta({"stem","cs_pathway","transfer_core"},           transfer=0.80, stem=0.75, cs=0.85, unlock=0.65, workload=0.50),
    "CS133G": _meta({"stem","cs_pathway"},                           transfer=0.65, stem=0.65, cs=0.75, unlock=0.30, workload=0.45),
    "CS133U": _meta({"stem","cs_pathway","foundational"},            transfer=0.80, stem=0.75, cs=0.88, unlock=0.65, workload=0.55),
    "CS160":  _meta({"stem","cs_pathway","transfer_core"},           transfer=0.75, stem=0.72, cs=0.82, unlock=0.55, workload=0.45),
    "CS161A": _meta({"stem","cs_pathway","foundational","transfer_core"}, transfer=0.87, stem=0.82, cs=0.95, unlock=0.92, workload=0.65),
    "CS161B": _meta({"stem","cs_pathway","foundational","transfer_core"}, transfer=0.87, stem=0.82, cs=0.95, unlock=0.88, workload=0.65),
    "CS162":  _meta({"stem","cs_pathway","foundational","transfer_core"}, transfer=0.88, stem=0.83, cs=0.96, unlock=0.90, workload=0.70),
    "CS201":  _meta({"stem","cs_pathway","transfer_core"},           transfer=0.85, stem=0.82, cs=0.90, unlock=0.60, workload=0.65),
    "CS233G": _meta({"stem","cs_pathway"},                           transfer=0.75, stem=0.72, cs=0.82, unlock=0.35, workload=0.55),
    "CS251":  _meta({"stem","cs_pathway","transfer_core"},           transfer=0.86, stem=0.83, cs=0.90, unlock=0.65, workload=0.70),
    "CS260":  _meta({"stem","cs_pathway","foundational","transfer_core"}, transfer=0.90, stem=0.85, cs=0.97, unlock=0.88, workload=0.75),

    # ── Mathematics ───────────────────────────────────────────────────────────
    "MTH60":  _meta({"stem","foundational"},                         transfer=0.40, stem=0.60, cs=0.40, unlock=0.75, workload=0.45),
    "MTH65":  _meta({"stem","foundational"},                         transfer=0.45, stem=0.65, cs=0.45, unlock=0.80, workload=0.45),
    "MTH95":  _meta({"stem","foundational","transfer_core"},         transfer=0.60, stem=0.70, cs=0.55, unlock=0.85, workload=0.50),
    "MTH111": _meta({"stem","foundational","cs_pathway","transfer_core"}, transfer=0.85, stem=0.82, cs=0.75, unlock=0.90, workload=0.60),
    "MTH112": _meta({"stem","foundational","cs_pathway","transfer_core"}, transfer=0.87, stem=0.85, cs=0.78, unlock=0.92, workload=0.65),
    "MTH211": _meta({"stem","foundational","cs_pathway","transfer_core","high_workload"}, transfer=0.93, stem=0.92, cs=0.88, unlock=0.95, workload=0.80),
    "MTH212": _meta({"stem","cs_pathway","transfer_core","high_workload"}, transfer=0.93, stem=0.92, cs=0.88, unlock=0.90, workload=0.82),
    "MTH213": _meta({"stem","cs_pathway","transfer_core","high_workload"}, transfer=0.93, stem=0.92, cs=0.88, unlock=0.85, workload=0.82),
    "MTH243": _meta({"stem","cs_pathway","transfer_core","high_workload"}, transfer=0.90, stem=0.90, cs=0.80, unlock=0.75, workload=0.78),
    "MTH244": _meta({"stem","cs_pathway","transfer_core","high_workload"}, transfer=0.90, stem=0.90, cs=0.80, unlock=0.70, workload=0.78),
    "MTH261": _meta({"stem","cs_pathway","transfer_core","high_workload"}, transfer=0.90, stem=0.90, cs=0.85, unlock=0.70, workload=0.80),
    "MTH252": _meta({"stem","cs_pathway","transfer_core","high_workload"}, transfer=0.92, stem=0.92, cs=0.85, unlock=0.85, workload=0.80),
    "MTH253": _meta({"stem","cs_pathway","transfer_core","high_workload"}, transfer=0.92, stem=0.92, cs=0.85, unlock=0.80, workload=0.80),

    # ── Writing ───────────────────────────────────────────────────────────────
    "WR115":  _meta({"writing","support_lab"},                       transfer=0.20, stem=0.0, cs=0.0, unlock=0.55, workload=0.35, base=0.20),
    "WR121":  _meta({"writing","foundational","transfer_core"},      transfer=0.82, stem=0.0, cs=0.15, unlock=0.85, workload=0.45),
    "WR122":  _meta({"writing","transfer_core"},                     transfer=0.80, stem=0.0, cs=0.15, unlock=0.60, workload=0.48),
    "WR227":  _meta({"writing","transfer_core"},                     transfer=0.75, stem=0.0, cs=0.15, unlock=0.40, workload=0.50),
    "WR241":  _meta({"writing","transfer_core"},                     transfer=0.72, stem=0.0, cs=0.10, unlock=0.30, workload=0.50),

    # ── Communication / Speech ────────────────────────────────────────────────
    "COMM100": _meta({"communication","transfer_core"},              transfer=0.65, stem=0.0, cs=0.10, unlock=0.50, workload=0.40),
    "COMM111": _meta({"communication","foundational","transfer_core"}, transfer=0.72, stem=0.0, cs=0.12, unlock=0.65, workload=0.42),
    "COMM112": _meta({"communication","transfer_core"},              transfer=0.68, stem=0.0, cs=0.10, unlock=0.50, workload=0.42),
    "SP111":   _meta({"communication","foundational","transfer_core"}, transfer=0.72, stem=0.0, cs=0.10, unlock=0.60, workload=0.40),

    # ── Social Sciences ───────────────────────────────────────────────────────
    "SOC204":  _meta({"social_sci","transfer_core"},                 transfer=0.68, stem=0.0, cs=0.08, unlock=0.35, workload=0.42),
    "SOC205":  _meta({"social_sci","transfer_core"},                 transfer=0.65, stem=0.0, cs=0.05, unlock=0.30, workload=0.42),
    "SOC206":  _meta({"social_sci","transfer_core"},                 transfer=0.65, stem=0.0, cs=0.05, unlock=0.25, workload=0.42),
    "SOC213":  _meta({"social_sci","transfer_core"},                 transfer=0.65, stem=0.0, cs=0.05, unlock=0.25, workload=0.42),
    "PSY101":  _meta({"social_sci","transfer_core"},                 transfer=0.68, stem=0.0, cs=0.08, unlock=0.35, workload=0.40),
    "PSY201A": _meta({"social_sci","transfer_core"},                 transfer=0.65, stem=0.0, cs=0.05, unlock=0.30, workload=0.42),
    "EC201":   _meta({"social_sci","transfer_core"},                 transfer=0.70, stem=0.0, cs=0.10, unlock=0.35, workload=0.45),
    "EC202":   _meta({"social_sci","transfer_core"},                 transfer=0.68, stem=0.0, cs=0.10, unlock=0.30, workload=0.45),
    "HST101":  _meta({"social_sci","arts_letters","transfer_core"},  transfer=0.65, stem=0.0, cs=0.05, unlock=0.25, workload=0.40),
    "HST102":  _meta({"social_sci","arts_letters","transfer_core"},  transfer=0.65, stem=0.0, cs=0.05, unlock=0.20, workload=0.40),
    "GEO105":  _meta({"social_sci","transfer_core"},                 transfer=0.62, stem=0.10, cs=0.05, unlock=0.20, workload=0.40),

    # ── Arts & Letters ────────────────────────────────────────────────────────
    "ENG104":  _meta({"arts_letters","transfer_core"},               transfer=0.62, stem=0.0, cs=0.05, unlock=0.30, workload=0.42),
    "ENG106":  _meta({"arts_letters","transfer_core"},               transfer=0.62, stem=0.0, cs=0.05, unlock=0.25, workload=0.42),
    "PHL201":  _meta({"arts_letters","transfer_core"},               transfer=0.65, stem=0.0, cs=0.15, unlock=0.20, workload=0.45),
    "PHL202":  _meta({"arts_letters","transfer_core"},               transfer=0.65, stem=0.0, cs=0.15, unlock=0.20, workload=0.45),
    "PHL203":  _meta({"arts_letters","transfer_core"},               transfer=0.65, stem=0.0, cs=0.15, unlock=0.20, workload=0.45),

    # ── PE / Health (wellness, filler unless required for graduation) ─────────
    "HE252":   _meta({"wellness","transfer_core"},                   transfer=0.45, stem=0.0, cs=0.0, unlock=0.10, workload=0.25, base=0.38),
    "HE295":   _meta({"wellness"},                                   transfer=0.30, stem=0.0, cs=0.0, unlock=0.10, workload=0.25, base=0.28),
}

# ── Subject-level fallback heuristics ─────────────────────────────────────────
# (transfer_value, stem, cs, workload, tags)
_SUBJECT_DEFAULTS: dict[str, tuple[float, float, float, float, set[str]]] = {
    "PHY":  (0.87, 0.93, 0.65, 0.65, {"stem", "stem_lab", "transfer_core"}),
    "CH":   (0.83, 0.88, 0.20, 0.70, {"stem", "stem_lab", "transfer_core"}),
    "BI":   (0.82, 0.85, 0.10, 0.60, {"stem", "stem_lab", "transfer_core"}),
    "G":    (0.74, 0.74, 0.10, 0.55, {"stem", "stem_lab", "transfer_core"}),
    "ES":   (0.70, 0.70, 0.10, 0.50, {"stem", "stem_lab", "transfer_core"}),
    "CS":   (0.83, 0.80, 0.92, 0.65, {"stem", "cs_pathway", "transfer_core"}),
    "CIS":  (0.75, 0.72, 0.82, 0.60, {"stem", "cs_pathway", "transfer_core"}),
    "MTH":  (0.80, 0.80, 0.65, 0.60, {"stem", "foundational", "transfer_core"}),
    "WR":   (0.70, 0.00, 0.12, 0.45, {"writing", "transfer_core"}),
    "COMM": (0.65, 0.00, 0.10, 0.40, {"communication", "transfer_core"}),
    "SP":   (0.65, 0.00, 0.10, 0.40, {"communication", "transfer_core"}),
    "SOC":  (0.62, 0.00, 0.06, 0.42, {"social_sci", "transfer_core"}),
    "PSY":  (0.62, 0.00, 0.06, 0.42, {"social_sci", "transfer_core"}),
    "HST":  (0.58, 0.00, 0.04, 0.40, {"social_sci", "arts_letters", "transfer_core"}),
    "ANTH": (0.58, 0.00, 0.04, 0.40, {"social_sci", "transfer_core"}),
    "EC":   (0.62, 0.00, 0.08, 0.43, {"social_sci", "transfer_core"}),
    "PS":   (0.55, 0.00, 0.04, 0.40, {"social_sci", "transfer_core"}),
    "GEO":  (0.55, 0.05, 0.04, 0.40, {"social_sci", "transfer_core"}),
    "ATH":  (0.50, 0.00, 0.03, 0.40, {"social_sci"}),
    "ART":  (0.48, 0.00, 0.04, 0.40, {"arts_letters", "transfer_core"}),
    "MUS":  (0.48, 0.00, 0.04, 0.38, {"arts_letters", "transfer_core"}),
    "HUM":  (0.52, 0.00, 0.05, 0.40, {"arts_letters", "transfer_core"}),
    "PHL":  (0.55, 0.00, 0.12, 0.43, {"arts_letters", "transfer_core"}),
    "ENG":  (0.56, 0.00, 0.08, 0.42, {"arts_letters", "writing", "transfer_core"}),
    "TA":   (0.45, 0.00, 0.04, 0.38, {"arts_letters"}),
    "PE":   (0.18, 0.00, 0.00, 0.25, {"wellness", "filler"}),
    "HE":   (0.28, 0.00, 0.00, 0.25, {"wellness"}),
}

_SUPPORT_LAB_PATTERN = re.compile(
    r"""
    (?:
        ^WR\d+[LXBCZ]\d*$        |  # WR199B, WR121L, WR115X, WR121Z support labs
        ^[A-Z]+\d+[XZ]\d*$       |  # PE100X, PE120Z activity variants
        ^MTH\d+[HBZ]$            |  # MTH65H bridge/support courses
        ^WR115$                   |  # developmental writing (always support)
        ^WR1[3-9]\d[A-Z]?$          # WR130-199 = developmental/support writing range
    )
    """,
    re.VERBOSE,
)


def get_course_metadata(course_code: str) -> CourseMetadata:
    """Return CourseMetadata for a course, with multi-level fallback.

    Priority:
    1. Exact match in COURSE_KB
    2. Support lab pattern detection
    3. Subject-level heuristics from _SUBJECT_DEFAULTS
    4. Generic low-utility default
    """
    code = course_code.upper().replace(" ", "")

    # 1. Exact match
    if code in COURSE_KB:
        return COURSE_KB[code]

    # 2. Support lab / low-value variant detection
    if _SUPPORT_LAB_PATTERN.match(code):
        return CourseMetadata(
            tags=frozenset({"support_lab", "filler"}),
            transfer_value=0.10,
            stem_relevance=0.0,
            cs_relevance=0.0,
            prereq_unlock=0.10,
            workload=0.25,
            base_utility=0.12,
        )

    # 3. Subject heuristic
    subj = re.match(r"^([A-Z]+)", code)
    if subj:
        key = subj.group(1)
        if key in _SUBJECT_DEFAULTS:
            t, s, c, w, tags = _SUBJECT_DEFAULTS[key]
            # Adjust workload upward for 200+ level courses
            num_m = re.search(r"(\d+)", code)
            num = int(num_m.group(1)) if num_m else 100
            if num >= 200:
                w = min(1.0, w + 0.10)
            unlock = 0.30 if "foundational" in tags else 0.15
            base = 0.40 * t + 0.30 * s + 0.20 * c + 0.10 * unlock
            return CourseMetadata(
                tags=frozenset(tags),
                transfer_value=t,
                stem_relevance=s,
                cs_relevance=c,
                prereq_unlock=unlock,
                workload=w,
                base_utility=min(1.0, base),
            )

    # 4. Generic fallback — unknown course
    return CourseMetadata(
        tags=frozenset(),
        transfer_value=0.40,
        stem_relevance=0.0,
        cs_relevance=0.0,
        prereq_unlock=0.10,
        workload=0.45,
        base_utility=0.35,
    )
