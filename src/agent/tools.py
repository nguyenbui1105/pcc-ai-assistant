"""
Tool definitions and executor for the PCC agent.
Each tool wraps existing src/* functions and returns a plain string for the LLM.
"""
import json
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from src.agent.session import AgentSession
from src.schedule.prereq_map import PREREQS

# ── Tool definitions (Ollama/OpenAI format) ───────────────────────────────────

TOOL_DEFS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_my_courses",
            "description": (
                "Get the student's currently enrolled courses for this term, "
                "including course name, ID, instructor, and class meeting days/times. "
                "Use this for ANY question about the student's current classes or when they meet — "
                "do NOT call find_courses for already-enrolled courses."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_degree_progress",
            "description": (
                "Get the student's degree audit: degree name, GPA, credits applied vs required, "
                "status of each requirement (complete/incomplete/in-progress), "
                "and the list of completed courses."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_finances",
            "description": (
                "Get the student's financial account: total charges, payments received, amount due."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_courses",
            "description": (
                "Search live PCC schedule for available courses by subject code(s). "
                "Returns open sections with CRN, time, location, instructor, credits. "
                "Automatically excludes courses the student has already completed. "
                "Use subject codes like WR, MTH, SOC, BI, CH, PHY, PE, HE, COMM, ENG, CS."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subjects": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "PCC subject codes, e.g. ['SOC', 'WR', 'BI']",
                    },
                    "term": {
                        "type": "string",
                        "description": "Term key: summer2026, fall2026, spring2027",
                        "default": "summer2026",
                    },
                },
                "required": ["subjects"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_schedule",
            "description": (
                "Build a conflict-free class schedule optimized for the student's degree gaps "
                "and personal preferences (credits, days off, time window, online/in-person). "
                "For F-1 students enforces ≥12 credits with ≥9 in-person."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "preferences": {
                        "type": "string",
                        "description": (
                            "Natural language preferences, e.g. "
                            "'12 credits, no Friday, done by 4pm, prefer online'"
                        ),
                    },
                    "term": {
                        "type": "string",
                        "description": "Term key: summer2026, fall2026, spring2027",
                        "default": "summer2026",
                    },
                },
                "required": ["preferences"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_pcc",
            "description": (
                "Search pcc.edu for policies, programs, tuition, deadlines, "
                "F-1 visa rules, admissions, or any general PCC information. "
                "Use when the question is about PCC policies, not the student's personal data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query, e.g. 'F-1 visa full-time enrollment requirement'",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_prerequisites",
            "description": (
                "Check prerequisite courses required before enrolling in a given course. "
                "Also checks if student has already met the prerequisites."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "course_code": {
                        "type": "string",
                        "description": "Course code, e.g. 'MTH252' or 'WR122'",
                    }
                },
                "required": ["course_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pcc_page",
            "description": (
                "Fetch the full content of a specific pcc.edu page URL. "
                "Use when you have a direct URL and need its detailed content — "
                "e.g. a department page, service page, or resource link."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full pcc.edu URL, e.g. 'https://www.pcc.edu/jobs/student-employment/'",
                    }
                },
                "required": ["url"],
            },
        },
    },
]


# ── Tool implementations ───────────────────────────────────────────────────────

def _tool_get_my_courses(session: AgentSession) -> str:
    ctx = session.personal_context
    if not ctx.courses:
        if not session.has_personal_data:
            return "MyPCC session expired. Please run `python scripts/setup_session.py` to refresh."
        return "No enrolled courses found for the current term."

    lines = [f"**Your courses — {ctx.current_term}**\n"]
    for c in ctx.courses:
        crn = c.course_id.split("-")[-1] if "-" in c.course_id else ""
        time_str = session.enrolled_times.get(crn, "")
        schedule_str = f" | {time_str}" if time_str else ""
        lines.append(f"- **{c.title}** ({c.course_id}){schedule_str}  ")
        lines.append(f"  Instructor: {c.instructor}")

    return "\n".join(lines)


def _tool_get_degree_progress(session: AgentSession) -> str:
    audit = session.degree_audit
    if not audit.degree:
        return "Degree audit not loaded. Please run `python scripts/setup_session.py` to refresh."

    lines = [
        f"Degree: {audit.degree}",
        f"Status: {audit.status}",
        f"GPA: {audit.gpa}",
        f"Credits: {audit.credits_applied} applied / {audit.credits_required} required "
        f"(need {max(0, audit.credits_required - audit.credits_applied)} more)",
        "",
        "Requirements:",
    ]
    for r in audit.requirements:
        icon = {"complete": "✓", "in-progress": "⏳", "incomplete": "✗"}.get(r.status, "?")
        line = f"  {icon} {r.name}: {r.status.upper()}"
        if r.still_needed:
            line += f" — still needed: {r.still_needed}"
        lines.append(line)

    lines.append("")
    lines.append(f"Completed courses ({len(audit.completed_courses)}): {', '.join(audit.completed_courses)}")
    return "\n".join(lines)


def _tool_get_my_finances(session: AgentSession) -> str:
    ctx = session.personal_context
    if not any([ctx.total_charges, ctx.payments_received, ctx.amount_due]):
        return "Financial data not available. Please run `python scripts/setup_session.py` to refresh."

    return "\n".join([
        f"Financial account ({ctx.current_term}):",
        f"  Total charges: {ctx.total_charges or 'N/A'}",
        f"  Payments received: {ctx.payments_received or 'N/A'}",
        f"  Amount due: {ctx.amount_due or 'N/A'}",
    ])


async def _tool_find_courses(subjects: list[str], term: str, session: AgentSession) -> str:
    from src.agent.course_recommender import _is_completed
    from src.schedule.fetcher import fetch_details_async, fetch_listings_async
    from src.schedule.parser import parse_detail_html, parse_listing_html

    subjects = [s.upper() for s in subjects[:6]]
    completed = set(session.degree_audit.completed_courses)

    listing_html = await fetch_listings_async(subjects, term)

    to_fetch = []
    subject_courses: dict[str, list] = {}
    for subj in subjects:
        courses = parse_listing_html(listing_html.get(subj, ""), subj)
        filtered = [c for c in courses if not _is_completed(c.course_code, completed)]
        # Sort by course number, pick top 3
        filtered.sort(key=lambda c: int(re.search(r"\d+", c.course_code).group() or 999))
        subject_courses[subj] = filtered[:3]
        to_fetch.extend(filtered[:3])

    urls = [c.detail_url for c in to_fetch if c.detail_url]
    if not urls:
        return f"No new courses found for {subjects} in {term}. All may already be completed."

    url_to_info = {c.detail_url: c for c in to_fetch if c.detail_url}
    html_map = await fetch_details_async(urls[:12])

    lines: list[str] = [f"Available courses for {term}:"]
    found_any = False

    for subj in subjects:
        courses = subject_courses[subj]
        if not courses:
            continue
        subj_lines: list[str] = []
        for course in courses:
            html = html_map.get(course.detail_url, "")
            if not html:
                continue
            sections = parse_detail_html(html, course.course_code, course.title)
            open_secs = [s for s in sections if s.is_open][:3]
            if not open_secs:
                continue
            credits = open_secs[0].credits or "?"
            subj_lines.append(f"\n  {course.course_code} — {course.title} ({credits} cr)")
            for sec in open_secs:
                mode = sec.section_type or "?"
                schedule = " ".join(filter(None, [sec.days, sec.time]))
                subj_lines.append(
                    f"    CRN {sec.crn} | {mode} | {schedule} | {sec.instructor} | {sec.date_range}"
                )
        if subj_lines:
            lines.append(f"\n[{subj}]")
            lines.extend(subj_lines)
            found_any = True

    if not found_any:
        return f"No open sections found for {subjects} in {term}. Schedule may not be posted yet."

    return "\n".join(lines)


async def _tool_build_schedule(preferences: str, term: str, session: AgentSession) -> str:
    from src.agent.schedule_planner import plan_schedule_async
    return await plan_schedule_async(
        session.degree_audit,
        preferences,
        term=term,
        is_international=session.is_international,
    )


async def _fetch_page_text(url: str, char_limit: int = 3000) -> str:
    """Fetch a PCC page and return clean text content."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; PCC-Assistant/1.0)"},
            )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["nav", "header", "footer", "script", "style", "aside", "form"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            return "\n".join(ln for ln in text.splitlines() if ln.strip())[:char_limit]
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
    return ""


# Keyword → known PCC URL for fallback when search engines return nothing
_PCC_TOPIC_URLS: list[tuple[list[str], str]] = [
    (["tuition", "fees", "cost", "credit hour", "rate", "học phí"], "https://www.pcc.edu/tuition/"),
    (["international", "f-1", "f1", "visa", "iss", "quốc tế"], "https://www.pcc.edu/international/"),
    (["financial aid", "fafsa", "scholarship", "grant", "loan", "học bổng"], "https://www.pcc.edu/paying-for-college/"),
    (["admission", "apply", "enroll", "register", "application", "nhập học"], "https://www.pcc.edu/admissions/"),
    (["advising", "counselor", "advisor", "academic plan", "cố vấn"], "https://www.pcc.edu/advising/"),
    (["job", "employment", "work study", "career", "việc làm"], "https://www.pcc.edu/jobs/student-employment/"),
    (["library", "tutoring", "learning center", "thư viện"], "https://www.pcc.edu/library/"),
    (["transfer", "ot", "aaot", "university", "chuyển trường"], "https://www.pcc.edu/programs/transfer/"),
]


def _guess_pcc_url(query: str) -> str | None:
    q = query.lower()
    for keywords, url in _PCC_TOPIC_URLS:
        if any(kw in q for kw in keywords):
            return url
    return None


async def _tool_search_pcc(query: str) -> str:
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(f"site:pcc.edu {query}", max_results=5))
    except Exception as e:
        logger.warning(f"DuckDuckGo search failed: {e}")
        results = []

    if not results:
        # Fallback 1: fetch a known PCC page matching the topic
        fallback_url = _guess_pcc_url(query)
        if fallback_url:
            logger.info(f"DuckDuckGo empty, fetching known PCC page: {fallback_url}")
            page_text = await _fetch_page_text(fallback_url, char_limit=4000)
            if page_text:
                return f"Source: {fallback_url}\n\n{page_text}"
        return f"No results found for '{query}'. Visit https://www.pcc.edu to search manually."

    # Build a summary of all results with links
    links_block = "### Related PCC pages:\n" + "\n".join(
        f"- [{r.get('title', r.get('href', ''))}]({r.get('href', '')})"
        for r in results
        if r.get("href")
    )

    # Fetch full content from the most relevant result
    top_url = results[0].get("href", "")
    page_text = await _fetch_page_text(top_url) if top_url else ""

    if page_text:
        return f"Source: {top_url}\n\n{page_text}\n\n{links_block}"

    # Fallback: return snippets from all results
    snippets = "\n\n".join(
        f"**{r.get('title', '')}** ({r.get('href', '')})\n{r.get('body', '')}"
        for r in results[:3]
        if r.get("body")
    )
    return f"{snippets}\n\n{links_block}"


async def _tool_get_pcc_page(url: str) -> str:
    """Fetch and return the content of a specific PCC page URL."""
    if "pcc.edu" not in url:
        return "Only pcc.edu pages are supported."
    text = await _fetch_page_text(url, char_limit=4000)
    if text:
        return f"Content from {url}:\n\n{text}"
    return f"Could not fetch content from {url}. Try opening it directly in a browser."


def _tool_check_prerequisites(course_code: str, session: AgentSession) -> str:
    code = course_code.upper().replace(" ", "")
    prereqs = PREREQS.get(code, [])
    completed = set(session.degree_audit.completed_courses)

    if not prereqs:
        return f"{code} has no listed prerequisites — you can enroll directly."

    met, unmet = [], []
    for p in prereqs:
        (met if p in completed else unmet).append(p)

    lines = [f"Prerequisites for {code}: {', '.join(prereqs)}"]
    if met:
        lines.append(f"  ✓ Met: {', '.join(met)}")
    if unmet:
        lines.append(f"  ✗ Still needed: {', '.join(unmet)}")
    if not unmet:
        lines.append("  → You meet all prerequisites.")
    return "\n".join(lines)


# ── Executor ──────────────────────────────────────────────────────────────────

async def execute_tool(name: str, args: dict, session: AgentSession) -> str:
    logger.info(f"[tool] {name}({args})")
    try:
        match name:
            case "get_my_courses":
                return _tool_get_my_courses(session)
            case "get_degree_progress":
                return _tool_get_degree_progress(session)
            case "get_my_finances":
                return _tool_get_my_finances(session)
            case "find_courses":
                return await _tool_find_courses(
                    args.get("subjects", []),
                    args.get("term", "summer2026"),
                    session,
                )
            case "build_schedule":
                return await _tool_build_schedule(
                    args.get("preferences", ""),
                    args.get("term", "summer2026"),
                    session,
                )
            case "search_pcc":
                return await _tool_search_pcc(args.get("query", ""))
            case "check_prerequisites":
                return _tool_check_prerequisites(args.get("course_code", ""), session)
            case "get_pcc_page":
                return await _tool_get_pcc_page(args.get("url", ""))
            case _:
                return f"Unknown tool: {name}"
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}")
        return f"Tool {name} encountered an error: {e}"
