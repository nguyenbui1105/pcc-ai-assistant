"""Course registration agent: degree gaps → live schedule → ranked recommendations."""
import asyncio
import re
from collections import defaultdict

from loguru import logger

from src.mypcc.parser import DegreeAudit
from src.schedule.fetcher import TERM_LABELS, fetch_details_async, fetch_listings_async
from src.schedule.gap_analyzer import SubjectGap, analyze_gaps, get_unique_subjects
from src.schedule.parser import CourseInfo, CourseSection, parse_detail_html, parse_listing_html


def _is_completed(course_code: str, completed: set[str]) -> bool:
    """Return True if this course was already completed, handling letter-suffix variants."""
    code = course_code.upper().replace(" ", "")
    if code in completed:
        return True
    # "WR121A" → also check "WR121"
    base = re.sub(r"[A-Z]$", "", code)
    return base in completed

# Max course detail pages to fetch per subject (avoid hammering the server)
_MAX_COURSES_TO_FETCH = 4
# Max sections to display per course
_MAX_SECTIONS_SHOWN = 3
# Max courses to display per requirement
_MAX_COURSES_SHOWN = 3


def _select_courses_for_fetch(
    courses: list[CourseInfo], max_n: int
) -> list[CourseInfo]:
    """Prefer lower-numbered intro courses (101–199) over upper-division."""
    def _course_num(ci: CourseInfo) -> int:
        m = re.search(r"\d+", ci.course_code)
        return int(m.group()) if m else 999

    return sorted(courses, key=_course_num)[:max_n]


async def _fetch_all_details(
    subject_courses: dict[str, list[CourseInfo]],
) -> dict[str, list[CourseSection]]:
    """Fetch detail pages for selected courses; return {course_code: [sections]}."""
    to_fetch: list[CourseInfo] = []
    for courses in subject_courses.values():
        to_fetch.extend(_select_courses_for_fetch(courses, _MAX_COURSES_TO_FETCH))

    urls = [ci.detail_url for ci in to_fetch if ci.detail_url]
    url_to_info: dict[str, CourseInfo] = {ci.detail_url: ci for ci in to_fetch}

    logger.info(f"Fetching {len(urls)} course detail pages...")
    html_map = await fetch_details_async(urls)

    result: dict[str, list[CourseSection]] = {}
    for url, html in html_map.items():
        info = url_to_info[url]
        sections = parse_detail_html(html, info.course_code, info.title)
        result[info.course_code] = sections

    return result


def _format_gap_block(
    gap: SubjectGap,
    subject_courses: dict[str, list[CourseInfo]],
    section_map: dict[str, list[CourseSection]],
    completed: set[str],
) -> str:
    """Format one requirement block with available courses and sections."""
    header = f"**{gap.label}**"
    if gap.still_needed:
        header += f" — {gap.still_needed}"

    lines: list[str] = [header]
    courses_shown = 0

    for subj in gap.subjects:
        if courses_shown >= _MAX_COURSES_SHOWN:
            break
        for course in subject_courses.get(subj, []):
            if courses_shown >= _MAX_COURSES_SHOWN:
                break
            if _is_completed(course.course_code, completed):
                continue
            sections = section_map.get(course.course_code, [])
            open_sections = [s for s in sections if s.is_open]
            if not sections:
                continue  # no detail fetched yet — skip
            if not open_sections and sections:
                # Course fetched but fully closed — show it grayed out
                lines.append(f"  ~~{course.course_code} {course.title}~~ _(no open seats)_")
                continue

            credits = open_sections[0].credits if open_sections else ""
            credits_note = f" ({credits} cr)" if credits else ""
            lines.append(f"  **{course.course_code} {course.title}**{credits_note}")

            for sec in open_sections[:_MAX_SECTIONS_SHOWN]:
                lines.append(f"    - {sec.summary()}")

            extra = len(open_sections) - _MAX_SECTIONS_SHOWN
            if extra > 0:
                lines.append(f"    - _…{extra} more open sections_")

            courses_shown += 1

    if courses_shown == 0:
        lines.append("  _No open sections found for this requirement this term._")

    return "\n".join(lines)


def _is_aaot_degree(degree: str) -> bool:
    return any(kw in degree.upper() for kw in ["AAOT", "OREGON TRANSFER", "ASSOCIATE OF ARTS OREGON"])


async def recommend_courses_async(
    audit: DegreeAudit,
    term: str | None = None,
) -> str:
    if not audit.degree:
        return (
            "I couldn't load your degree audit. "
            "Please re-run `scripts/setup_session.py` to refresh your session cookies."
        )

    if term is None:
        from src.schedule.fetcher import get_current_term
        term = get_current_term()

    gaps = analyze_gaps(audit)
    if not gaps:
        if _is_aaot_degree(audit.degree):
            return (
                "Your degree audit shows all AAOT requirements are **complete** — "
                "congratulations! Double-check at "
                "[GRAD Plan](https://gradplan.pcc.edu) for final confirmation."
            )
        return (
            f"Automatic course recommendations work best for AAOT degrees. "
            f"Your degree (**{audit.degree}**) may not be fully supported.\n\n"
            f"**Known progress:** {audit.credits_applied}/{audit.credits_required} credits "
            f"| GPA {audit.gpa}\n\n"
            f"Please consult your advisor, or use **find_courses** to search for "
            f"specific subjects directly."
        )

    subjects = get_unique_subjects(gaps)
    term_label = TERM_LABELS.get(term, term)

    logger.info(f"Fetching listings for {len(subjects)} subjects: {subjects}")
    listing_html = await fetch_listings_async(subjects, term)

    # Parse listings
    subject_courses: dict[str, list[CourseInfo]] = {}
    for subj in subjects:
        subject_courses[subj] = parse_listing_html(listing_html.get(subj, ""), subj)
        logger.debug(f"  {subj}: {len(subject_courses[subj])} courses listed")

    # Fetch course detail pages
    section_map = await _fetch_all_details(subject_courses)

    # Build output
    lines: list[str] = [
        f"## Course Recommendations — {term_label}",
        "",
        f"**Degree:** {audit.degree}  ",
        f"**Progress:** {audit.credits_applied}/{audit.credits_required} credits applied | GPA {audit.gpa}",
        "",
        "---",
        "",
    ]

    completed_set = set(audit.completed_courses)
    for gap in gaps:
        lines.append(_format_gap_block(gap, subject_courses, section_map, completed_set))
        lines.append("")

    lines.append("---")
    lines.append(
        "_Register at [MyPCC](https://my.pcc.edu) → Student Records → Add/Drop Classes._  "
        "Use the CRN number when adding a class."
    )
    return "\n".join(lines)


def recommend_courses(audit: DegreeAudit, term: str | None = None) -> str:
    return asyncio.run(recommend_courses_async(audit, term))
