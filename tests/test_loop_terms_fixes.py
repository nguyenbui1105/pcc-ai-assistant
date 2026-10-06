"""Regression tests: provider fallback isolation in agent_stream, term tables that
follow the clock, the in-session term, and the demo session's term and statuses."""
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent import loop
from src.agent.providers.groq_provider import GroqProvider
from src.agent.session import AgentSession, create_demo_session
from src.mypcc.parser import _course_id_to_code
from src.schedule import fetcher

_TABLES = (fetcher.TERM_CODES, fetcher.TERM_NAMES, fetcher.TERM_LABELS)


@pytest.fixture(autouse=True)
def _restore_term_tables():
    """Clock-patched tests extend the module tables in place; put them back afterwards."""
    saved = [dict(t) for t in _TABLES]
    yield
    for table, original in zip(_TABLES, saved):
        table.clear()
        table.update(original)


def _set_clock(monkeypatch, *ymd):
    class _Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(*ymd)

    monkeypatch.setattr(fetcher, "datetime", _Fixed)


def _load_stale_tables(center_year: int) -> None:
    """Make the module tables look as if the process was imported in center_year."""
    for table, stale in zip(_TABLES, fetcher._build_term_tables(center_year)):
        table.clear()
        table.update(stale)


# ── agent_stream: a failed provider must not poison the next one ─────────────

class _RecordingProvider:
    def __init__(self):
        self.received = None

    async def stream_with_tools(self, messages, session, max_iterations=6):
        self.received = list(messages)
        yield "fallback answer"


class _PoisoningProvider:
    """Appends a non-dict SDK message (as Groq/9Router do), then fails mid-turn."""

    async def stream_with_tools(self, messages, session, max_iterations=6):
        messages.append(object())
        raise RuntimeError("503 after a tool round")
        yield  # pragma: no cover — makes this an async generator


@pytest.mark.anyio
async def test_failed_provider_does_not_leak_messages_into_fallback():
    session = AgentSession()
    expected = loop._build_messages("How much is tuition?", session)
    fallback = _RecordingProvider()
    with patch.object(loop, "_build_provider_chain", return_value=[_PoisoningProvider(), fallback]):
        out = [t async for t in loop.agent_stream("How much is tuition?", session)]

    assert "".join(out) == "fallback answer"
    assert len(fallback.received) == len(expected)
    assert all(isinstance(m, dict) for m in fallback.received)
    assert fallback.received[-1] == expected[-1]


@pytest.mark.anyio
async def test_groq_failing_after_tool_round_falls_back_with_clean_messages():
    tc = MagicMock()
    tc.function.name = "search_pcc"
    tc.function.arguments = json.dumps({"query": "tuition"})
    tc.id = "call_1"
    msg = MagicMock()
    msg.content = ""
    msg.tool_calls = [tc]
    first = MagicMock()
    first.choices = [MagicMock(message=msg)]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        side_effect=[first, RuntimeError("Error code: 503 - Service Unavailable")]
    )
    fallback = _RecordingProvider()

    with patch.object(loop, "_build_provider_chain",
                      return_value=[GroqProvider(client=client), fallback]), \
         patch("src.agent.providers.base.execute_tool", AsyncMock(return_value="Source: pcc.edu")):
        out = [t async for t in loop.agent_stream("How much is tuition?", AgentSession())]

    assert out[-1] == "fallback answer"
    # Ollama's client rejects SDK message objects — the fallback must see plain dicts only.
    assert all(isinstance(m, dict) for m in fallback.received)
    assert [m["role"] for m in fallback.received][-1] == "user"


# ── Term tables follow the clock ──────────────────────────────────────────────

def test_current_term_label_exists_after_year_rollover(monkeypatch):
    _load_stale_tables(2026)          # process imported during 2026
    _set_clock(monkeypatch, 2028, 1, 1)

    term = fetcher.get_current_term()
    assert term == "spring2028"
    assert fetcher.TERM_LABELS[term] == "Spring 2028"
    # Modules that imported the dicts see the new keys (same objects, updated in place).
    assert loop.TERM_LABELS is fetcher.TERM_LABELS
    assert "Spring 2028" in loop._build_messages("hi", AgentSession())[0]["content"]
    assert fetcher.TERM_CODES.keys() == fetcher.TERM_NAMES.keys() == fetcher.TERM_LABELS.keys()
    assert "winter2025" in fetcher.TERM_CODES   # keys a running session may hold are kept


def test_demo_session_builds_after_year_rollover(monkeypatch):
    _load_stale_tables(2026)
    _set_clock(monkeypatch, 2028, 1, 1)
    assert create_demo_session().personal_context.current_term == "Winter 2028"


def test_resolve_term_accepts_next_year_after_rollover(monkeypatch):
    _load_stale_tables(2026)
    _set_clock(monkeypatch, 2028, 5, 1)
    assert fetcher.resolve_term("Fall 2029") == "fall2029"
    assert fetcher.TERM_CODES["fall2029"] == "202904"


def test_mypcc_term_label_lookup_follows_tables(monkeypatch):
    from src.mypcc.fetcher import _term_key_for_label

    _load_stale_tables(2026)
    _set_clock(monkeypatch, 2028, 1, 1)
    assert _term_key_for_label("Spring 2028") == "spring2028"
    assert _term_key_for_label("Fall 2026") == "fall2026"
    assert _term_key_for_label("") == ""
    assert _term_key_for_label("Fall 1999") == ""


def test_sync_leaves_current_tables_untouched(monkeypatch):
    _set_clock(monkeypatch, 2026, 10, 6)
    fetcher.get_current_term()        # make sure 2026's window is present
    monkeypatch.setitem(fetcher.TERM_CODES, "fall2026", "patched")
    assert fetcher.get_current_term() == "fall2026"
    assert fetcher.TERM_CODES["fall2026"] == "patched"


# ── In-session term vs planning default ───────────────────────────────────────

@pytest.mark.parametrize("ymd, expected", [
    ((2027, 1, 5), "winter2027"), ((2027, 3, 31), "winter2027"),
    ((2027, 4, 1), "spring2027"), ((2027, 6, 15), "spring2027"),
    ((2027, 6, 16), "summer2027"), ((2027, 8, 31), "summer2027"),
    ((2027, 9, 1), "fall2027"), ((2027, 12, 31), "fall2027"),
])
def test_get_in_session_term_by_date(ymd, expected, monkeypatch):
    _set_clock(monkeypatch, *ymd)
    term = fetcher.get_in_session_term()
    assert term == expected
    assert term in fetcher.TERM_LABELS


@pytest.mark.parametrize("ymd, enrolled, planning", [
    ((2027, 2, 10), "Winter 2027", "Spring 2027"),
    ((2027, 5, 12), "Spring 2027", "Summer 2027"),
    ((2026, 10, 6), "Fall 2026", "Fall 2026"),
])
def test_demo_term_is_in_session_term(ymd, enrolled, planning, monkeypatch):
    _set_clock(monkeypatch, *ymd)
    session = create_demo_session()
    ctx = session.personal_context
    assert ctx.current_term == enrolled
    assert ctx.upcoming_dates[0].startswith(enrolled)
    # get_current_term() stays the planning default for tools.
    assert fetcher.TERM_LABELS[fetcher.get_current_term()] == planning
    assert f"Tools default to {planning}" in loop._build_messages("hi", session)[0]["content"]


# ── Demo GRAD Plan statuses agree with enrolled courses ───────────────────────

def test_demo_requirements_for_enrolled_courses_are_in_progress():
    session = create_demo_session()
    enrolled = {_course_id_to_code(c.course_id) for c in session.personal_context.courses}
    statuses = {r.name: r.status for r in session.degree_audit.requirements}
    assert statuses["College Mathematics"] == "in-progress"   # MTH111 is enrolled
    assert statuses["Composition"] == "in-progress"           # WR122 is enrolled
    for r in session.degree_audit.requirements:
        if any(code in r.still_needed.replace(" ", "") for code in enrolled):
            assert r.status == "in-progress", r.name


def test_demo_open_requirements_unchanged():
    session = create_demo_session()
    open_reqs = {
        "Composition", "College Mathematics", "Health and Wellness",
        "Arts and Letters", "Lab Requirement 2", "Lab Requirement 3",
    }
    assert {r.name for r in session.degree_audit.requirements if r.status != "complete"} == open_reqs
    assert {r.name for r in session.academic_state.remaining_reqs} == open_reqs
