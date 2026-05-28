"""Interactive schedule planner: asks preferences → builds conflict-free schedule."""
import asyncio

from loguru import logger

from src.academic.student_model import infer_profile, enrich_profile_from_text
from src.academic.utility_scorer import sort_sections_by_utility, is_high_priority_gap
from src.mypcc.parser import DegreeAudit
from src.schedule.fetcher import TERM_LABELS, fetch_capacity_async, fetch_details_async, fetch_listings_async, get_current_term
from src.schedule.gap_analyzer import analyze_gaps, get_unique_subjects, course_to_requirement
from src.schedule.optimizer import (
    SchedulePrefs,
    SchedulePlan,
    build_schedule,
    parse_prefs,
    section_credits,
)
from src.schedule.parser import CourseInfo, CourseSection, parse_detail_html, parse_listing_html
from src.schedule.prereq_map import PREREQS

_MAX_COURSES_PER_SUBJECT = 6

_INTL_NOTICE = """\
> **F-1 International Student:** You must enroll in **at least 12 credits**, \
with **at least 9 credits in-person** (face-to-face). \
Online-only enrollment is not allowed to maintain full-time status.

"""

PLANNER_PROMPT_DOMESTIC = """\
To build your personalized schedule, I need a few details:

**1. How many credits do you want this term?**
   (6 = light / 9 = moderate / 12 = full-time / 15 = heavy)

**2. Online, in-person, or no preference?**

**3. Any days or times you're NOT available?**
   e.g. "not available Tuesday and Thursday", "nothing before 10am", "done by 4pm"

*Reply in one message — e.g.: "9 credits, prefer online, not available Tuesday morning"*
"""

PLANNER_PROMPT_INTERNATIONAL = (
    _INTL_NOTICE
    + """\
To build your schedule, I need a few details:

**1. How many credits?** *(minimum 12 as an F-1 student)*

**2. Any days or times you're NOT available?**
   e.g. "not available Tuesday and Thursday", "nothing before 10am", "done by 4pm"

**3. Any mode preference for the online portion?** *(max ~3 credits online)*

*Reply in one message — e.g.: "12 credits, not available Friday, prefer morning classes"*
"""
)

# Alias used by ui/app.py (defaults to domestic; overridden in get_planner_prompt)
PLANNER_PROMPT = PLANNER_PROMPT_DOMESTIC


def get_planner_prompt(is_international: bool) -> str:
    return PLANNER_PROMPT_INTERNATIONAL if is_international else PLANNER_PROMPT_DOMESTIC


def _is_aaot_degree(degree: str) -> bool:
    return any(kw in degree.upper() for kw in ["AAOT", "OREGON TRANSFER", "ASSOCIATE OF ARTS OREGON"])


def _extract_completed_from_audit(audit: DegreeAudit) -> list[str]:
    """Return course codes the student has completed or is currently taking."""
    return list(audit.completed_courses)


async def _collect_sections(
    audit: DegreeAudit, term: str, profile=None
) -> list[CourseSection]:
    """Fetch candidate sections, selecting courses by utility score not just course number."""
    from src.academic.utility_scorer import score_course as _score_course
    import re as _re

    gaps = analyze_gaps(audit)
    subjects = get_unique_subjects(gaps)

    listing_html = await fetch_listings_async(subjects, term)

    subject_courses: dict[str, list[CourseInfo]] = {}
    for subj in subjects:
        courses = parse_listing_html(listing_html.get(subj, ""), subj)
        if profile is not None:
            # Sort by utility score (highest first) so we fetch the most valuable courses
            def _key(c: CourseInfo) -> float:
                s, _ = _score_course(c.course_code, profile)
                return -s
            courses_sorted = sorted(courses, key=_key)
        else:
            # Fallback: lowest course number first
            def _num_key(c: CourseInfo) -> int:
                m = _re.search(r"\d+", c.course_code)
                return int(m.group()) if m else 999
            courses_sorted = sorted(courses, key=_num_key)
        subject_courses[subj] = courses_sorted[:_MAX_COURSES_PER_SUBJECT]

    to_fetch = [c for courses in subject_courses.values() for c in courses]
    urls = [c.detail_url for c in to_fetch if c.detail_url]
    url_to_info = {c.detail_url: c for c in to_fetch}

    html_map = await fetch_details_async(urls)

    all_sections: list[CourseSection] = []
    for url, html in html_map.items():
        info = url_to_info[url]
        sections = parse_detail_html(html, info.course_code, info.title)
        all_sections.extend(sections)

    # Enrich with real seat counts from capacity API
    crns = [s.crn for s in all_sections if s.crn]
    if crns:
        capacity = await fetch_capacity_async(crns, term)
        _merge_capacity(all_sections, capacity)

    return all_sections


def _merge_capacity(sections: list[CourseSection], capacity: dict) -> None:
    """Write real seat counts from capacity API response into section objects."""
    for sec in sections:
        data = capacity.get(sec.crn)
        if not data:
            continue
        seat = data.get("seat", [-1, -1])
        wait = data.get("wait", [-1, -1])
        if len(seat) >= 2:
            sec.seats_available = int(seat[0])
            sec.seats_total = int(seat[1])
        if len(wait) >= 2:
            sec.waitlist_available = int(wait[0])
            sec.waitlist_total = int(wait[1])


def _inperson_credits(sections: list[CourseSection]) -> int:
    return sum(section_credits(s) for s in sections if s.section_type != "Online")


def _format_plan(
    plan: SchedulePlan,
    prefs: SchedulePrefs,
    term_label: str,
    gaps: list,
) -> str:
    """Format the schedule plan as readable markdown."""
    inperson_cr = _inperson_credits(plan.sections)
    online_cr = plan.total_credits - inperson_cr

    mode_label = prefs.mode if prefs.mode != "any" else "no preference"
    lines: list[str] = [
        f"## Your Suggested Schedule — {term_label}",
        "",
        f"**Target:** {prefs.credit_target} credits | "
        f"**Mode:** {mode_label} | "
        f"**Blocked days:** {', '.join(sorted(prefs.blocked_days)) or 'none'}",
        "",
        f"**Total: {plan.total_credits} credits** "
        f"({inperson_cr} in-person · {online_cr} online)",
    ]

    # International compliance check
    if prefs.is_international:
        if plan.total_credits < 12 or inperson_cr < 9:
            lines.append("")
            lines.append(
                f"⚠️ **F-1 compliance issue:** {plan.total_credits} credits total "
                f"({inperson_cr} in-person). Minimum: 12 total / 9 in-person. "
                f"Please consult ISS before enrolling."
            )
        else:
            lines.append(
                f"  ✅ *Meets F-1 requirement: ≥12 credits, ≥9 in-person*"
            )

    lines += ["", "---", ""]

    for sec in plan.sections:
        cr = section_credits(sec)
        req_label = course_to_requirement(sec.course_code, gaps)
        req_tag = f" `→ {req_label}`" if req_label else ""

        lines.append(f"### {sec.course_code} — {sec.title} ({cr} cr){req_tag}")

        detail_parts = [f"CRN **{sec.crn}**"]
        if sec.section_type:
            detail_parts.append(sec.section_type)
        if sec.location:
            detail_parts.append(sec.location)
        schedule = " ".join(filter(None, [sec.days, sec.time]))
        if schedule:
            detail_parts.append(schedule)
        if sec.date_range:
            detail_parts.append(sec.date_range)
        if sec.instructor:
            detail_parts.append(f"👨‍🏫 {sec.instructor}")
        if sec.seats_available >= 0 and sec.seats_total > 0:
            detail_parts.append(f"🪑 {sec.seats_available}/{sec.seats_total} seats open")
        elif sec.seats == 0:
            detail_parts.append("⛔ Full")
        # Only show waitlist when class is full or someone is actually on it
        if sec.waitlist_total > 0 and (
            sec.seats_available == 0 or sec.waitlist_available < sec.waitlist_total
        ):
            taken = sec.waitlist_total - sec.waitlist_available
            detail_parts.append(f"waitlist {taken}/{sec.waitlist_total}")
        if sec.fees:
            detail_parts.append(f"fees {sec.fees}")
        lines.append("  " + " | ".join(detail_parts))

        if sec.course_code in plan.missing_prereqs:
            missing = plan.missing_prereqs[sec.course_code]
            lines.append(
                f"  ⚠️ **Prereq needed:** {', '.join(missing)} "
                f"— confirm with advisor before enrolling"
            )

        lines.append("")

    lines.append("---")

    gap = prefs.credit_target - plan.total_credits
    if gap > 0:
        lines.append(
            f"_This plan is {plan.total_credits} credits — {gap} short of your target. "
            f"Consider adding an elective or PE/Health course._"
        )
    elif gap < 0:
        lines.append(
            f"_This plan is {plan.total_credits} credits — {abs(gap)} over your target._"
        )

    lines += [
        "",
        "_Register at [MyPCC](https://my.pcc.edu) → Student Records → Add/Drop.  "
        "Use CRN numbers above._",
    ]
    return "\n".join(lines)


async def plan_schedule_async(
    audit: DegreeAudit,
    prefs_text: str,
    term: str | None = None,
    is_international: bool = False,
    extra_excluded: list[str] | None = None,
) -> str:
    """Full pipeline: parse prefs → fetch schedule → optimize → format."""
    if term is None:
        term = get_current_term()
    if not audit.degree:
        return (
            "I couldn't load your degree audit. "
            "Please re-run `scripts/setup_session.py` to refresh your session."
        )

    prefs = parse_prefs(prefs_text, is_international=is_international)
    term_label = TERM_LABELS.get(term, term)
    logger.info(
        f"Planning schedule: {prefs.credit_target}cr, mode={prefs.mode}, "
        f"blocked={prefs.blocked_days}, intl={prefs.is_international}"
    )

    # Build student profile for utility-aware section selection
    profile = infer_profile(audit, is_f1=is_international)
    profile = enrich_profile_from_text(profile, prefs_text)
    logger.info(
        f"Student profile: major={profile.major_interest}, "
        f"stem={profile.stem_affinity:.2f}, transfer={profile.transfer_priority:.2f}"
    )

    gaps = analyze_gaps(audit)
    if not gaps:
        if _is_aaot_degree(audit.degree):
            return (
                "Your degree audit shows all AAOT requirements are **complete** — "
                "no gaps to fill. Consult your advisor if you need electives or "
                "want to confirm graduation requirements at "
                "[GRAD Plan](https://gradplan.pcc.edu)."
            )
        return (
            f"Automatic schedule building works best for AAOT degrees. "
            f"Your degree (**{audit.degree}**) may not be fully supported.\n\n"
            f"**Known progress:** {audit.credits_applied}/{audit.credits_required} credits "
            f"| GPA {audit.gpa}\n\n"
            f"Please consult your advisor, or use **Find Courses** to search for "
            f"specific subjects directly."
        )

    all_sections = await _collect_sections(audit, term, profile=profile)
    logger.info(f"Collected {len(all_sections)} candidate sections")

    if not all_sections:
        return (
            f"No sections found for {term_label}. "
            "The schedule may not be posted yet — try again later."
        )

    completed = _extract_completed_from_audit(audit)
    if extra_excluded:
        normalized = {c.upper().replace(" ", "") for c in extra_excluded}
        completed = list(set(completed) | normalized)

    # Sort sections by utility score BEFORE the greedy optimizer.
    # This ensures high-value courses (lab sciences, CS, MTH) are picked first
    # instead of whatever subject happens to appear first in the gaps list.
    high_priority_codes = {
        s.course_code for s in all_sections
        if any(is_high_priority_gap(g.label, profile) and s.course_code.startswith(tuple(g.subjects))
               for g in gaps)
    }
    all_sections_sorted = sort_sections_by_utility(
        all_sections, profile, high_priority_codes=high_priority_codes
    )

    plan = build_schedule(all_sections_sorted, prefs, completed)

    if not plan or not plan.sections:
        msg = (
            f"Could not find a conflict-free combination matching your preferences."
        )
        if is_international:
            msg += (
                " As an F-1 student, you need ≥12 credits with ≥9 in-person — "
                "try removing day/time restrictions."
            )
        else:
            msg += " Try relaxing constraints (e.g., allow more days, fewer blocked times)."
        return msg

    gaps = analyze_gaps(audit)
    return _format_plan(plan, prefs, term_label, gaps)


def plan_schedule(
    audit: DegreeAudit,
    prefs_text: str,
    term: str | None = None,
    is_international: bool = False,
) -> str:
    return asyncio.run(plan_schedule_async(audit, prefs_text, term, is_international))
