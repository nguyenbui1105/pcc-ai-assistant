import sys
sys.path.insert(0, ".")

import time
import chainlit as cl
from loguru import logger

from src.agent.loop import agent_stream
from src.agent.session import AgentSession, create_demo_session
from src.mypcc.fetcher import (
    fetch_degree_audit_async,
    fetch_personal_context_async,
    fetch_banss_schedule_async,
    fetch_enrolled_times_async,
    SessionExpiredError,
)
from src.mypcc.parser import parse, parse_degree_audit, parse_banss_events, _course_id_to_code
from src.schedule.fetcher import TERM_CODES, TERM_LABELS
from src.agent.query_logger import QueryLogger

_COOKIES_PATH = "./data/mypcc_cookies.json"
_GRADPLAN_COOKIES_PATH = "./data/gradplan_cookies.json"
_BANSS_COOKIES_PATH = "./data/banss_cookies.json"
_query_logger = QueryLogger()

_TOOL_LABELS = {
    "get_my_courses": "Fetching your courses...",
    "get_degree_progress": "Checking degree progress...",
    "get_my_finances": "Fetching financial info...",
    "find_courses": "Searching PCC schedule...",
    "build_schedule": "Building your schedule...",
    "search_pcc": "Searching PCC website...",
    "check_prerequisites": "Checking prerequisites...",
    "get_pcc_page": "Reading PCC page...",
}


async def _load_real_session() -> tuple[AgentSession | None, str]:
    """Try to load personal data from cookies.

    Returns (session, reason) where:
    - session is None on failure; reason explains why (for the UI)
    - reason is "" on full success, or "gradplan-expired" if only GRAD Plan failed
    """
    try:
        pages = await fetch_personal_context_async(_COOKIES_PATH)
        ctx = parse(pages)
        if not ctx.has_meaningful_data():
            return None, "expired"

        session = AgentSession(
            personal_context=ctx,
            student_name=ctx.student_name,
            is_international=ctx.is_international,
        )

        gradplan_warning = ""
        try:
            audit_text = await fetch_degree_audit_async(_GRADPLAN_COOKIES_PATH)
            ctx.degree_audit = parse_degree_audit(audit_text)
            session.degree_audit = ctx.degree_audit
            for course in ctx.courses:
                code = _course_id_to_code(course.course_id)
                if code and code not in session.degree_audit.completed_courses:
                    session.degree_audit.completed_courses.append(code)
            logger.info(
                f"GRAD Plan loaded: {session.degree_audit.credits_applied}/"
                f"{session.degree_audit.credits_required} credits"
            )
        except SessionExpiredError:
            gradplan_warning = "gradplan-expired"
            logger.warning("GRAD Plan JWT expired — degree audit unavailable")
        except Exception as e:
            logger.warning(f"GRAD Plan unavailable: {e}")

        try:
            times = await fetch_enrolled_times_async(ctx.courses, ctx.current_term)
            session.enrolled_times = times
            logger.info(f"Enrolled times loaded: {len(times)} courses")
        except Exception as e:
            logger.warning(f"Could not fetch enrolled times: {e}")

        logger.info(f"Real session loaded for: {session.student_name}")
        return session, gradplan_warning

    except FileNotFoundError:
        logger.warning("MyPCC cookies file not found")
        return None, "missing"
    except Exception as e:
        logger.warning(f"Could not load real session: {e}")
        return None, "error"


_DEMO_REASON_MESSAGES: dict[str, str] = {
    "missing": (
        "> **Cookies not found.** Run `python scripts/setup_session.py` to log in to MyPCC "
        "and save your session. Until then, the assistant runs with a sample student profile."
    ),
    "expired": (
        "> **Session expired.** Your MyPCC cookies are no longer valid. "
        "Run `python scripts/setup_session.py` to refresh your session."
    ),
    "error": (
        "> **Could not load your PCC session.** "
        "Run `python scripts/setup_session.py` to set up your cookies."
    ),
}

_GRADPLAN_EXPIRED_NOTE = (
    "\n\n> **Note:** Your GRAD Plan session has expired — degree audit is unavailable. "
    "Run `python scripts/setup_session.py` to refresh (GRAD Plan JWT expires every ~7 days)."
)


@cl.on_chat_start
async def on_start():
    session, reason = await _load_real_session()

    if session is not None:
        # Real student data loaded successfully (possibly without GRAD Plan)
        cl.user_session.set("agent_session", session)
        name_part = f", {session.student_name}" if session.student_name else ""
        body = (
            f"Hi{name_part}! I'm your PCC Assistant.\n\n"
            "I have access to your **MyPCC data** — ask me about your courses, "
            "balance, degree progress, or what to register for next."
        )
        if reason == "gradplan-expired":
            body += _GRADPLAN_EXPIRED_NOTE
        await cl.Message(content=body).send()
    else:
        # Cookies missing or expired → load demo mode
        session = create_demo_session()
        cl.user_session.set("agent_session", session)
        logger.info(f"Demo mode activated: {reason}")
        reason_note = _DEMO_REASON_MESSAGES.get(reason, _DEMO_REASON_MESSAGES["error"])
        await cl.Message(
            content=(
                "Hi! I'm your PCC Assistant — running in **Demo Mode**.\n\n"
                f"{reason_note}\n\n"
                "Try asking:\n"
                "- *What courses am I taking this term?*\n"
                "- *What do I still need to graduate?*\n"
                "- *Find me open Chemistry classes for Summer 2026*\n"
                "- *Build me a 12-credit schedule for Fall 2026*"
            )
        ).send()


@cl.on_message
async def on_message(message: cl.Message):
    question = message.content.strip()
    if not question:
        return

    session: AgentSession = cl.user_session.get("agent_session", create_demo_session())
    t0 = time.monotonic()

    msg = cl.Message(content="")
    await msg.send()

    full_reply = ""
    async with cl.Step(name="Looking up data...") as step:
        async for token in agent_stream(question, session):
            # Intercept tool status signals — update step label, don't stream to message
            if token.startswith("\x00tool:") and token.endswith("\x00"):
                tool_name = token[6:-1]
                step.name = _TOOL_LABELS.get(tool_name, f"Using {tool_name}...")
                await step.update()
                continue
            if not msg.content and token.strip():
                await step.remove()
            await msg.stream_token(token)
            full_reply += token

    await msg.update()

    session.add_turn(question, full_reply)
    cl.user_session.set("agent_session", session)

    latency = time.monotonic() - t0
    _query_logger.log(question, "agent", latency)
    logger.info(f"Reply in {latency:.1f}s | demo={session.is_demo}")
