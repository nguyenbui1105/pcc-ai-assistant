"""Static prerequisite map for common PCC/AAOT courses."""
import re as _re

_NORM_RE = _re.compile(r'^([A-Za-z]+)0+(\d+\w*)$')


def normalize_code(code: str) -> str:
    """Strip leading zeros from the numeric part: 'MTH065' → 'MTH65', 'WR121' → 'WR121'."""
    m = _NORM_RE.match(code.strip().upper())
    return (m.group(1) + m.group(2)) if m else code.strip().upper()

# Maps course_code → list of accepted prereqs (any one satisfies)
# Empty list = no prereq required
# Based on PCC course catalog: https://www.pcc.edu/schedule/
PREREQS: dict[str, list[str]] = {
    # ── Writing / Composition ─────────────────────────────────────────────
    "WR115": [],
    "WR121": ["WR115"],           # or placement
    "WR122": ["WR121"],
    "WR123": ["WR121"],
    "WR214": ["WR121"],
    "WR222": ["WR121"],
    "WR227": ["WR121"],
    "WR241": ["WR121"],
    "WR243": ["WR121"],

    # ── Communication ─────────────────────────────────────────────────────
    "COMM100": [],
    "COMM111": [],
    "COMM112": ["COMM111"],
    "COMM218": [],
    "COMM219": [],

    # ── Mathematics ───────────────────────────────────────────────────────
    "MTH065": [],
    "MTH095": ["MTH065"],
    "MTH111": ["MTH095"],
    "MTH112": ["MTH111"],
    "MTH211": ["MTH112"],
    "MTH241": ["MTH112"],
    "MTH251": ["MTH112"],

    # ── Biology ───────────────────────────────────────────────────────────
    "BI101": [],
    "BI102": ["BI101"],
    "BI103": ["BI102"],
    "BI112": [],                  # Cell Bio for Health — no formal prereq
    "BI120": [],
    "BI164": [],
    "BI211": ["WR115"],           # Principles of Biology — WR placement
    "BI212": ["BI211"],
    "BI213": ["BI212"],
    "BI231": ["WR115", "BI112"],  # A&P I — WR 115 or placement + BI 112 rec
    "BI232": ["BI231"],
    "BI233": ["BI232"],
    "BI234": ["BI231"],

    # ── Chemistry ─────────────────────────────────────────────────────────
    "CH100": [],
    "CH104": ["MTH065"],          # Allied Health Chem I
    "CH105": ["CH104"],
    "CH106": ["CH105"],
    "CH221": ["MTH112", "CH104"], # General Chem I
    "CH222": ["CH221"],
    "CH223": ["CH222"],

    # ── Physics ───────────────────────────────────────────────────────────
    "PHY100": [],
    "PHY121": [],
    "PHY122": ["PHY121"],
    "PHY123": ["PHY122"],
    "PHY201": ["MTH251"],
    "PHY202": ["PHY201"],
    "PHY203": ["PHY202"],

    # ── Geology / Earth Science ───────────────────────────────────────────
    "G201": [],
    "G202": [],
    "G203": [],
    "ES101": [],
    "ES252": ["WR115"],

    # ── Sociology ─────────────────────────────────────────────────────────
    "SOC204": [],
    "SOC205": ["SOC204"],
    "SOC206": ["SOC204"],
    "SOC207": ["SOC204"],
    "SOC213": ["SOC204"],

    # ── Psychology ────────────────────────────────────────────────────────
    "PSY101": [],
    "PSY201A": ["PSY101"],
    "PSY202A": ["PSY101"],
    "PSY213": ["PSY101"],

    # ── History ───────────────────────────────────────────────────────────
    "HST100": [],
    "HST101": [],
    "HST102": ["HST101"],
    "HST103": ["HST102"],

    # ── Geography ─────────────────────────────────────────────────────────
    "GEO105": [],
    "GEO106": [],
    "GEO170": [],

    # ── Economics ─────────────────────────────────────────────────────────
    "EC200": [],
    "EC201": ["MTH065"],
    "EC202": ["EC201"],

    # ── Political Science ─────────────────────────────────────────────────
    "PS201": [],
    "PS202": [],

    # ── Philosophy ────────────────────────────────────────────────────────
    "PHL191": [],
    "PHL195": [],
    "PHL201": [],
    "PHL202": [],

    # ── Art ───────────────────────────────────────────────────────────────
    "ART101": [],
    "ART102": ["ART101"],
    "ART103": ["ART102"],
    "ART116": [],
    "ART131": [],

    # ── Music ─────────────────────────────────────────────────────────────
    "MUS101": [],
    "MUS105": [],
    "MUS108": [],
    "MUS110": [],

    # ── Theatre Arts ─────────────────────────────────────────────────────
    "TA101": [],
    "TA145": [],

    # ── Humanities ───────────────────────────────────────────────────────
    "HUM100": [],
    "HUM221": ["WR121"],

    # ── English / Literature ──────────────────────────────────────────────
    "ENG104": ["WR115"],
    "ENG106": ["WR115"],
    "ENG205": ["WR121"],

    # ── Health & PE ───────────────────────────────────────────────────────
    "HE100": [],
    "HE101": [],
    "PE120A": [],
    "PE120B": [],
    "PE120C": [],
    "PE185A": [],
}


def get_superseded_courses(completed: list[str]) -> set[str]:
    """Return ALL transitive prerequisites of any completed course (normalized).

    Example: MTH112 done → MTH111, MTH095/MTH95, MTH065/MTH65 all superseded.
    Both long ('MTH065') and short ('MTH65') forms are returned.
    """
    completed_norm = {normalize_code(c) for c in completed}
    # Build a normalized version of PREREQS for lookup
    prereqs_norm = {normalize_code(k): [normalize_code(p) for p in v]
                    for k, v in PREREQS.items()}
    superseded: set[str] = set()
    queue = list(completed_norm)
    while queue:
        code = queue.pop()
        for prereq in prereqs_norm.get(code, []):
            if prereq not in superseded:
                superseded.add(prereq)
                queue.append(prereq)
    return superseded


def get_missing_prereqs(course_code: str, completed: list[str]) -> list[str]:
    """Return list of prereqs that the student hasn't completed yet."""
    norm_code = normalize_code(course_code)
    prereqs_norm = {normalize_code(k): [normalize_code(p) for p in v]
                    for k, v in PREREQS.items()}
    prereqs = prereqs_norm.get(norm_code, [])
    if not prereqs:
        return []
    completed_norm = {normalize_code(c) for c in completed}
    return [p for p in prereqs if p not in completed_norm]


def has_prereqs_met(course_code: str, completed: list[str]) -> bool:
    """True if student meets prereqs (or course has none)."""
    return len(get_missing_prereqs(course_code, completed)) == 0
