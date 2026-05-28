"""Query classifier: routes to personal data, course recommendations, or schedule planning."""
import re

# ── Personal query ────────────────────────────────────────────────────────────

_PERSONAL_EN = re.compile(
    r"(?:"
    r"\bmy\s+(?:courses?|classes?|balance|advisor|credits?|degree|progress|requirements?|schedule|grades?|enrollment)\b"
    r"|\bam\s+i\s+(?:enrolled|taking|registered)\b"
    r"|\bi\s+still\s+need\b"
    r"|\bi\s+have\b"
    r")",
    re.I,
)

_PERSONAL_VI = re.compile(
    r"(?:của\s+tôi|tôi\s+đang\s+học|tôi\s+học|kỳ\s+này\s+tôi)",
    re.I,
)


def is_personal_query(q: str) -> bool:
    """Return True if the question is about the student's own PCC data."""
    return bool(_PERSONAL_EN.search(q) or _PERSONAL_VI.search(q))


# ── Course recommendation query ───────────────────────────────────────────────

_RECOMMEND_EN = re.compile(
    r"(?:"
    r"\brecommend\b"
    r"|\bshould\s+(?:i\s+)?take\b"
    r"|\bcan\s+i\s+enroll\b"
    r"|\bstill\s+need\s+to\s+complete\b"
    r"|\bstill\s+need\b"
    r"|\bcourses?\s+available\b"
    r"|\bclasses?\s+available\b"
    r"|\bsuggests?\b"
    r")",
    re.I,
)

_RECOMMEND_VI = re.compile(
    r"(?:gợi\s+ý|môn\s+học|thiếu\s+môn|tốt\s+nghiệp)",
    re.I,
)


def is_course_recommend_query(q: str) -> bool:
    """Return True if the question is asking for course recommendations."""
    return bool(_RECOMMEND_EN.search(q) or _RECOMMEND_VI.search(q))


# ── Schedule plan query ───────────────────────────────────────────────────────

_SCHEDULE_EN = re.compile(
    r"(?:"
    r"\b(?:build|create|make)\s+(?:(?:me|my|a)\s+)*schedule\b"
    r"|\bplan\s+(?:my\s+)?(?:schedule|semester|term|timetable)\b"
    r"|\b\d+\s+credits?\b"
    r"|\bnot\s+available\b"
    r"|\bprefer\b"
    r"|\bdone\s+by\b"
    r")",
    re.I,
)

_SCHEDULE_VI = re.compile(
    r"(?:lập\s+lịch|đăng\s+ký|tín\s+chỉ)",
    re.I,
)


def is_schedule_plan_query(q: str) -> bool:
    """Return True if the question is asking to build a schedule."""
    return bool(_SCHEDULE_EN.search(q) or _SCHEDULE_VI.search(q))
