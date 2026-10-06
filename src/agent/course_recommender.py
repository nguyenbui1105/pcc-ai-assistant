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

from loguru import logger

from src.academic.redundancy import RedundancyChecker
from src.academic.student_model import StudentProfile, enrich_profile_from_text, infer_profile
from src.academic.utility_scorer import is_high_priority_gap, rank_courses_for_display, score_course
from src.mypcc.parser import DegreeAudit
from src.schedule.fetcher import TERM_LABELS, fetch_details_async, fetch_listings_async
from src.schedule.gap_analyzer import SubjectGap, analyze_gaps, get_unique_subjects
from src.schedule.parser import CourseInfo, CourseSection, parse_detail_html, parse_listing_html
from src.schedule.prereq_map import get_missing_prereqs

# Max course detail pages per subject (performance limit)
_MAX_COURSES_TO_FETCH = 5
# Max sections shown per course in output
_MAX_SECTIONS_SHOWN = 2
# Max courses shown per requirement block
_MAX_COURSES_SHOWN = 3
# Minimum utility score — courses below this are suppressed unless nothing better exists
_MIN_SCORE_THRESHOLD = 0.28


def _is_completed(course_code: str, completed: set[str]) -> bool:
    """Check if a course is completed. Uses normalized codes to handle MTH065 ↔ MTH65."""
    from src.schedule.prereq_map import normalize_code
    code = normalize_code(course_code)
    if code in completed:
        return True
    # Also check with normalized completed set
    completed_norm = {normalize_code(c) for c in completed}
    if code in completed_norm:
        return True
    base = re.sub(r"[A-Z]$", "", code)
    return base in completed_norm


def _select_courses_for_fetch(
    courses: list[CourseInfo],
    profile: StudentProfile,
    max_n: int,
    completed: set[str] | frozenset[str] = frozenset(),
    checker: "RedundancyChecker | None" = None,
) -> list[CourseInfo]:
    """Select courses to fetch, ordered by utility score (best-first).

    Unlike the old approach (just lowest course number), this prefers
    courses with higher strategic value for the student's profile.
    Completed/superseded courses and courses whose prerequisites aren't met
    (completed includes in-progress) are skipped, so the fetch budget goes to
    courses the student can take next (MTH112 after MTH111, not MTH211).
    """
    def _sort_key(ci: CourseInfo) -> float:
        s, _ = score_course(ci.course_code, profile)
        return -s  # negate for ascending sort (highest score first)

    eligible = [
        ci for ci in courses
        if not (checker and checker.is_redundant(ci.course_code))
        and not _is_completed(ci.course_code, completed)
        and not get_missing_prereqs(ci.course_code, list(completed))
    ]
    return sorted(eligible, key=_sort_key)[:max_n]


async def _fetch_all_details(
    subject_courses: dict[str, list[CourseInfo]],
    profile: StudentProfile,
    completed: set[str] | frozenset[str] = frozenset(),
    checker: "RedundancyChecker | None" = None,
) -> dict[str, list[CourseSection]]:
    """Fetch detail pages for selected courses, prioritizing by utility score."""
    to_fetch: list[CourseInfo] = []
    for courses in subject_courses.values():
        to_fetch.extend(_select_courses_for_fetch(
            courses, profile, _MAX_COURSES_TO_FETCH, completed, checker
        ))

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
    checker: "RedundancyChecker | None" = None,
) -> str:
    """Format one requirement block with utility-ranked courses + WHY explanations."""
    high_priority = is_high_priority_gap(gap.requirement, profile)
    priority_badge = " 🔴 **HIGH PRIORITY**" if high_priority else ""

    header = f"**{gap.label}**{priority_badge}"
    if gap.still_needed:
        header += f" — {gap.still_needed}"

    lines: list[str] = [header]

    # Prefer disciplines the student hasn't covered yet (multi-discipline reqs)
    active_subjects = (
        checker.filter_redundant_subjects(gap)
        if checker else gap.subjects
    )

    # Collect available courses — only from subjects still needed
    candidate_courses: list[CourseInfo] = []
    needs_prereqs: list[str] = []
    for subj in active_subjects:
        for course in subject_courses.get(subj, []):
            # Skip if directly completed or superseded by higher-level course
            if checker and checker.is_redundant(course.course_code):
                continue
            if _is_completed(course.course_code, completed):
                continue
            # Not eligible yet: a prerequisite is neither completed nor in progress
            missing = get_missing_prereqs(course.course_code, list(completed))
            if missing:
                needs_prereqs.append(f"{course.course_code} (needs {', '.join(missing)})")
                continue
            candidate_courses.append(course)

    if not candidate_courses:
        if needs_prereqs:
            lines.append(
                "  _No courses you're eligible for yet. Prerequisites first: "
                f"{'; '.join(needs_prereqs[:3])}._"
            )
        else:
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


def _no_gaps_message(audit: DegreeAudit) -> str:
    """Reply when no requirement block needs new courses.

    Congratulates only when the audit is really done: GRAD Plan says COMPLETE,
    or no credits are short and no tracked requirement (including unmapped ones
    such as "AAOT Credit Total") is still open.
    """
    if not _is_aaot_degree(audit.degree):
        return (
            f"Automatic course recommendations work best for AAOT degrees. "
            f"Your degree (**{audit.degree}**) may not be fully supported.\n\n"
            f"**Known progress:** {audit.credits_applied}/{audit.credits_required} credits "
            f"| GPA {audit.gpa}\n\n"
            f"Please consult your advisor, or use **find_courses** to search for "
            f"specific subjects directly."
        )

    credits_left = max(0, audit.credits_required - audit.credits_applied)
    open_reqs = [r for r in audit.requirements if r.status != "complete"]
    audit_open = audit.status.upper() in ("INCOMPLETE", "IN-PROGRESS")
    if audit.status.upper() == "COMPLETE" or not (audit_open or credits_left or open_reqs):
        return (
            "Your degree audit shows all AAOT requirements are **complete** — "
            "congratulations! Double-check at "
            "[GRAD Plan](https://gradplan.pcc.edu) for final confirmation."
        )

    in_progress = [r for r in open_reqs if r.status.replace("-", "_") == "in_progress"]
    still_open = [r for r in open_reqs if r not in in_progress]
    lines = [
        "Your AAOT requirement areas are complete or in progress, but your degree "
        "is **not finished yet**.",
        "",
        f"**Progress:** {audit.credits_applied}/{audit.credits_required} credits "
        f"({credits_left} still needed) | GPA {audit.gpa}",
    ]
    if in_progress:
        lines.append(
            f"**In progress this term:** {', '.join(r.name for r in in_progress)} — "
            "should be complete once your current classes are finished."
        )
    if still_open:
        lines.append("**Still open:** " + "; ".join(
            f"{r.name} — {r.still_needed}" if r.still_needed else r.name for r in still_open
        ))
    lines += [
        "",
        "Remaining credits can come from any transferable college-level course. "
        "Tell me your interests for suggestions, or use **find_courses** to search "
        "a subject. Confirm at [GRAD Plan](https://gradplan.pcc.edu).",
    ]
    return "\n".join(lines)


async def recommend_courses_async(
    audit: DegreeAudit,
    term: str | None = None,
    profile_hints: str = "",
    is_f1: bool = False,
    academic_state=None,  # AcademicState | None — canonical source of truth
) -> str:
    if not audit.degree:
        return (
            "I couldn't load your degree audit. "
            "Please re-run `scripts/setup_session.py` to refresh your session cookies."
        )

    if term is None:
        from src.schedule.fetcher import get_current_term
        term = get_current_term()

    # Requirements GRAD Plan reports as in progress will be completed by the
    # student's current classes, so they get no new recommendations. Read from the
    # GRAD Plan audit itself, before it is replaced by the AcademicState view below.
    grad_in_progress = {
        r.name for r in audit.requirements if r.status.replace("-", "_") == "in_progress"
    }

    # Build student profile
    profile = infer_profile(audit, is_f1=is_f1)
    if profile_hints:
        profile = enrich_profile_from_text(profile, profile_hints)

    # Use canonical AcademicState when available — gives us completed_all
    # (completed + in_progress) and pre-filtered remaining requirements.
    if academic_state is not None:
        from src.mypcc.parser import DegreeRequirement as _DR
        from src.mypcc.parser import DegreeAudit as _DA
        audit = _DA(
            degree=academic_state.degree,
            gpa=academic_state.gpa,
            credits_applied=academic_state.credits_applied,
            credits_required=academic_state.credits_required,
            status=academic_state.audit_status,
            requirements=[
                _DR(name=r.name, status=r.status, still_needed=r.still_needed)
                for r in academic_state.requirements
            ],
            completed_courses=list(academic_state.completed_all),
        )

    gaps = analyze_gaps(audit)
    in_progress_gaps = [g for g in gaps if g.requirement in grad_in_progress]
    gaps = [g for g in gaps if g.requirement not in grad_in_progress]
    if not gaps:
        return _no_gaps_message(audit)

    # Build redundancy checker — filters completed/superseded courses
    checker = (
        RedundancyChecker.from_state(academic_state)
        if academic_state is not None
        else RedundancyChecker.from_audit(audit)
    )

    # Sort gaps: HIGH-PRIORITY first, then alphabetical by label
    def _gap_sort_key(g: SubjectGap) -> tuple[int, str]:
        hp = 0 if is_high_priority_gap(g.requirement, profile) else 1
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

    completed_set = set(audit.completed_courses)
    section_map = await _fetch_all_details(subject_courses, profile, completed_set, checker)

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
    ]
    if in_progress_gaps:
        lines += [
            "_In progress this term (your current classes should complete these): "
            f"{', '.join(g.label for g in in_progress_gaps)}._",
            "",
        ]
    lines += ["---", ""]

    for gap in gaps_sorted:
        lines.append(_format_gap_block(gap, subject_courses, section_map, completed_set, profile, checker))
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
