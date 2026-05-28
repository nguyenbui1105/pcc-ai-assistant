"""Course recommendation engine — utility-aware, profile-driven.

Pipeline:
  1. Infer StudentProfile from degree audit + session flags
  2. Prioritize gaps by requirement urgency and student profile
  3. Fetch live schedule sections for each gap's subjects
  4. Score every section with multi-factor utility scorer
  5. Sort high-utility courses first within each requirement block
  6. Output ranked recommendations with WHY explanations
"""
import asyncio
import re
from collections import defaultdict

from loguru import logger

from src.academic.course_knowledge import get_course_metadata
from src.academic.student_model import StudentProfile, enrich_profile_from_text, infer_profile
from src.academic.utility_scorer import is_high_priority_gap, rank_courses_for_display, score_course
from src.mypcc.parser import DegreeAudit
from src.schedule.fetcher import TERM_LABELS, fetch_details_async, fetch_listings_async
from src.schedule.gap_analyzer import SubjectGap, analyze_gaps, get_unique_subjects
from src.schedule.parser import CourseInfo, CourseSection, parse_detail_html, parse_listing_html

# Max course detail pages per subject (performance limit)
_MAX_COURSES_TO_FETCH = 5
# Max sections shown per course in output
_MAX_SECTIONS_SHOWN = 2
# Max courses shown per requirement block
_MAX_COURSES_SHOWN = 3
# Minimum utility score — courses below this are suppressed unless nothing better exists
_MIN_SCORE_THRESHOLD = 0.28


def _is_completed(course_code: str, completed: set[str]) -> bool:
    code = course_code.upper().replace(" ", "")
    if code in completed:
        return True
    base = re.sub(r"[A-Z]$", "", code)
    return base in completed


def _select_courses_for_fetch(
    courses: list[CourseInfo],
    profile: StudentProfile,
    max_n: int,
) -> list[CourseInfo]:
    """Select courses to fetch, ordered by utility score (best-first).

    Unlike the old approach (just lowest course number), this prefers
    courses with higher strategic value for the student's profile.
    """
    def _sort_key(ci: CourseInfo) -> float:
        s, _ = score_course(ci.course_code, profile)
        return -s  # negate for ascending sort (highest score first)

    return sorted(courses, key=_sort_key)[:max_n]


async def _fetch_all_details(
    subject_courses: dict[str, list[CourseInfo]],
    profile: StudentProfile,
) -> dict[str, list[CourseSection]]:
    """Fetch detail pages for selected courses, prioritizing by utility score."""
    to_fetch: list[CourseInfo] = []
    for courses in subject_courses.values():
        to_fetch.extend(_select_courses_for_fetch(courses, profile, _MAX_COURSES_TO_FETCH))

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
    profile: StudentProfile,
) -> str:
    """Format one requirement block with utility-ranked courses + WHY explanations."""
    high_priority = is_high_priority_gap(gap.label, profile)
    priority_badge = " 🔴 **HIGH PRIORITY**" if high_priority else ""

    header = f"**{gap.label}**{priority_badge}"
    if gap.still_needed:
        header += f" — {gap.still_needed}"

    lines: list[str] = [header]

    # Collect all available courses for this gap
    candidate_courses: list[CourseInfo] = []
    for subj in gap.subjects:
        for course in subject_courses.get(subj, []):
            if not _is_completed(course.course_code, completed):
                candidate_courses.append(course)

    if not candidate_courses:
        lines.append("  _No courses available for this requirement this term._")
        return "\n".join(lines)

    # Score and rank all candidate courses
    course_codes = [c.course_code for c in candidate_courses]
    ranked = rank_courses_for_display(course_codes, profile, gap.label, high_priority)

    # Filter below threshold (unless there's nothing else)
    above = [(code, sc, rs) for code, sc, rs in ranked if sc >= _MIN_SCORE_THRESHOLD]
    if not above:
        above = ranked  # fallback: show something

    courses_shown = 0
    has_low_score_suppressed = False

    for course_code, utility_score, reasons in above:
        if courses_shown >= _MAX_COURSES_SHOWN:
            break

        sections = section_map.get(course_code, [])
        open_sections = [s for s in sections if s.is_open]

        if not sections:
            continue

        # Find CourseInfo to get title
        course_info = next(
            (c for c in candidate_courses if c.course_code == course_code), None
        )
        title = course_info.title if course_info else course_code

        if not open_sections:
            lines.append(f"  ~~{course_code} {title}~~ _(no open seats this term)_")
            continue

        credits = open_sections[0].credits or "?"
        credits_note = f" ({credits} cr)" if credits else ""

        # Score badge
        score_bar = "★★★" if utility_score >= 0.70 else "★★" if utility_score >= 0.50 else "★"

        lines.append(f"  **{course_code} {title}**{credits_note}  {score_bar}")

        # WHY explanation (show first 2 reasons)
        why_parts = [r for r in reasons[1:3] if r]  # skip "satisfies requirement" (already in header)
        if why_parts:
            lines.append(f"  _Why: {' · '.join(why_parts)}_")

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
    profile_hints: str = "",
    is_f1: bool = False,
) -> str:
    if not audit.degree:
        return (
            "I couldn't load your degree audit. "
            "Please re-run `scripts/setup_session.py` to refresh your session cookies."
        )

    if term is None:
        from src.schedule.fetcher import get_current_term
        term = get_current_term()

    # Build student profile
    profile = infer_profile(audit, is_f1=is_f1)
    if profile_hints:
        profile = enrich_profile_from_text(profile, profile_hints)

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

    # Sort gaps: HIGH-PRIORITY first, then alphabetical by label
    def _gap_sort_key(g: SubjectGap) -> tuple[int, str]:
        hp = 0 if is_high_priority_gap(g.label, profile) else 1
        return (hp, g.label)

    gaps_sorted = sorted(gaps, key=_gap_sort_key)

    subjects = get_unique_subjects(gaps_sorted)
    term_label = TERM_LABELS.get(term, term)

    logger.info(f"Recommending for profile: major={profile.major_interest}, "
                f"stem={profile.stem_affinity:.2f}, transfer={profile.transfer_priority:.2f}")
    logger.info(f"Fetching listings for {len(subjects)} subjects: {subjects}")
    listing_html = await fetch_listings_async(subjects, term)

    subject_courses: dict[str, list[CourseInfo]] = {}
    for subj in subjects:
        subject_courses[subj] = parse_listing_html(listing_html.get(subj, ""), subj)
        logger.debug(f"  {subj}: {len(subject_courses[subj])} courses listed")

    section_map = await _fetch_all_details(subject_courses, profile)

    # Header
    stem_note = ""
    if profile.is_stem_oriented:
        stem_note = f" · **{profile.major_interest}** track"
    transfer_note = f" · Transfer → {profile.transfer_target}" if profile.transfer_target else ""

    lines: list[str] = [
        f"## Course Recommendations — {term_label}",
        "",
        f"**Degree:** {audit.degree}  ",
        f"**Progress:** {audit.credits_applied}/{audit.credits_required} credits | GPA {audit.gpa}"
        f"{stem_note}{transfer_note}",
        "",
        "_Courses ranked by strategic value for your profile. "
        "★★★ = highest priority, ★ = lower priority._",
        "",
        "---",
        "",
    ]

    completed_set = set(audit.completed_courses)
    for gap in gaps_sorted:
        lines.append(_format_gap_block(gap, subject_courses, section_map, completed_set, profile))
        lines.append("")

    lines.append("---")
    lines.append(
        "_Register at [MyPCC](https://my.pcc.edu) → Student Records → Add/Drop Classes._  "
        "Use the CRN number when adding a class."
    )
    return "\n".join(lines)


def recommend_courses(
    audit: DegreeAudit,
    term: str | None = None,
    profile_hints: str = "",
    is_f1: bool = False,
) -> str:
    return asyncio.run(recommend_courses_async(audit, term, profile_hints, is_f1))
