"""Regression tests for agent tool output (tools.py) and provider tool-loop handling."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from groq.types.chat import ChatCompletion as GroqChatCompletion
from ollama import ChatResponse, Message
from openai.types.chat import ChatCompletion as OpenAIChatCompletion

from src.agent.providers.groq_provider import GroqProvider
from src.agent.providers.nine_router_provider import NineRouterProvider
from src.agent.providers.ollama_provider import OllamaProvider
from src.agent.session import AgentSession, create_demo_session
from src.agent.tools import execute_tool
from src.mypcc.parser import CourseInfo, DegreeAudit, DegreeRequirement, PersonalContext
from src.schedule.fetcher import TERM_LABELS, get_current_term
from src.schedule.parser import CourseInfo as ListingCourse

pytestmark = pytest.mark.anyio

APOLOGY = "couldn't generate an answer"


def make_session(completed=(), courses=(), requirements=()) -> AgentSession:
    """A real-style session (no AcademicState): enrolled courses are also in completed_courses, as ui/app.py does."""
    audit = DegreeAudit(
        degree="Associate of Arts Oregon Transfer (AAOT)",
        requirements=list(requirements),
        completed_courses=list(completed),
    )
    ctx = PersonalContext(courses=[CourseInfo(f"Course {cid}", cid, "Staff") for cid in courses])
    return AgentSession(personal_context=ctx, degree_audit=audit)


async def tool(name: str, args: dict, session: AgentSession | None = None) -> str:
    return await execute_tool(name, args, session or create_demo_session())


# ── check_prerequisites: normalized codes (findings 0, 26) ─────────────────────

async def test_check_prereqs_unpadded_completed_code_counts_as_met():
    # Demo completed "MTH95"; PREREQS lists MTH111 → ["MTH095"]
    out = await tool("check_prerequisites", {"course_code": "MTH111"})
    assert "Still needed" not in out
    assert "✓ Met: MTH95" in out
    assert "You meet all prerequisites" in out


async def test_check_prereqs_padded_completed_code_counts_as_met():
    out = await tool("check_prerequisites", {"course_code": "MTH 95"}, make_session(completed=["MTH065"]))
    assert "Prerequisites for MTH95: MTH65" in out
    assert "✓ Met: MTH65" in out
    assert "Still needed" not in out


@pytest.mark.parametrize("raw", ["MTH 95", "mth95", "MTH095", "MTH-95"])
async def test_check_prereqs_spaced_or_padded_input_finds_entry(raw):
    out = await tool("check_prerequisites", {"course_code": raw}, make_session())
    assert "Prerequisites for MTH95: MTH65" in out
    assert "✗ Still needed: MTH65" in out


@pytest.mark.parametrize("raw", ["wr-122", "WR-122-12345", "WR 122"])
async def test_check_prereqs_mypcc_style_ids(raw):
    out = await tool("check_prerequisites", {"course_code": raw}, make_session(completed=["WR121"]))
    assert "Prerequisites for WR122: WR121" in out
    assert "✓ Met: WR121" in out


# ── check_prerequisites: unknown courses, completed / in progress (findings 3, 27) ──

@pytest.mark.parametrize("code", ["XYZ999", "MTH252"])
async def test_check_prereqs_unmapped_course_is_unknown_not_open(code):
    out = await tool("check_prerequisites", {"course_code": code}, make_session())
    assert "enroll directly" not in out
    if code == "XYZ999":
        assert "No prerequisite data for XYZ999" in out
        assert "pcc.edu/courses" in out


async def test_check_prereqs_empty_list_still_means_no_prereqs():
    out = await tool("check_prerequisites", {"course_code": "COMM100"}, make_session())
    assert "COMM100 has no listed prerequisites" in out


async def test_check_prereqs_mentions_already_completed_course():
    out = await tool("check_prerequisites", {"course_code": "CS162"})  # demo completed CS162
    assert "CS162 is already completed" in out
    assert "enroll directly" not in out


async def test_check_prereqs_in_progress_prereq_is_not_reported_as_met():
    # Demo student is only enrolled in MTH111 this term
    out = await tool("check_prerequisites", {"course_code": "mth 112"})
    assert "✓ Met: MTH111" not in out
    assert "In progress this term (met once passed): MTH111" in out
    assert "once your in-progress courses are passed" in out


async def test_check_prereqs_in_progress_without_academic_state():
    s = make_session(completed=["MTH95", "MTH111"], courses=["MTH-111-23456"])
    out = await tool("check_prerequisites", {"course_code": "MTH111"}, s)
    assert "MTH111 is in progress this term" in out
    out = await tool("check_prerequisites", {"course_code": "MTH112"}, s)
    assert "✓ Met: MTH111" not in out
    assert "In progress this term (met once passed): MTH111" in out


# ── get_degree_progress: in-progress courses listed separately (finding 1) ─────

def _course_line(out: str, prefix: str) -> list[str]:
    line = next(ln for ln in out.splitlines() if ln.startswith(prefix))
    return line.split(": ", 1)[1].split(", ")


async def test_degree_progress_lists_in_progress_separately_demo():
    out = await tool("get_degree_progress", {})
    completed = _course_line(out, "Completed courses")
    assert not {"WR122", "MTH111", "CS260"} & set(completed)
    assert "MTH95" in completed
    assert _course_line(out, "In progress this term") == ["CS260", "MTH111", "WR122"]


async def test_degree_progress_lists_in_progress_separately_without_state():
    s = make_session(completed=["WR121", "MTH095", "WR122"], courses=["WR-122-12345"])
    out = await tool("get_degree_progress", {}, s)
    assert _course_line(out, "Completed courses") == ["MTH95", "WR121"]
    assert _course_line(out, "In progress this term") == ["WR122"]


async def test_degree_progress_in_progress_status_icon():
    s = make_session(requirements=[
        DegreeRequirement("Composition", "in_progress", still_needed="WR122"),
        DegreeRequirement("College Mathematics", "in-progress"),
    ])
    out = await tool("get_degree_progress", {}, s)
    assert "⏳ Composition: IN-PROGRESS" in out
    assert "⏳ College Mathematics: IN-PROGRESS" in out
    assert "?" not in out


# ── get_my_courses: demo notice on a passthrough tool (finding 2) ──────────────

async def test_my_courses_demo_starts_with_demo_notice():
    out = await tool("get_my_courses", {})
    assert out.splitlines()[0].startswith("> 📌 **Demo data**")
    assert "Your courses" not in out
    assert "WR-122-12345" in out


async def test_my_courses_real_session_has_no_demo_notice():
    s = make_session(courses=["WR-122-12345"])
    s.personal_context.current_term = "Fall 2026"
    out = await tool("get_my_courses", {}, s)
    assert "Demo data" not in out
    assert out.startswith("**Your courses — Fall 2026**")


# ── find_courses: accurate empty-result messages (finding 4) ───────────────────

def _listings(html: str):
    calls: list[list[str]] = []

    async def fake(subjects, term=None):
        calls.append(list(subjects))
        return {s: html for s in subjects}

    return fake, calls


async def test_find_courses_failed_listing_is_not_blamed_on_completion():
    fake, _ = _listings("")
    with patch("src.schedule.fetcher.fetch_listings_async", fake):
        out = await tool("find_courses", {"subjects": ["CH"]})
    assert "already completed" not in out
    assert "Could not load the PCC schedule for CH" in out
    assert TERM_LABELS[get_current_term()] in out
    assert "['CH']" not in out


async def test_find_courses_listing_without_courses():
    fake, _ = _listings("<html><body>No classes</body></html>")
    with patch("src.schedule.fetcher.fetch_listings_async", fake):
        out = await tool("find_courses", {"subjects": ["CHEM"]})
    assert "already completed" not in out
    assert f"No CHEM courses are listed for {TERM_LABELS[get_current_term()]}" in out


async def test_find_courses_all_completed_says_so():
    fake, _ = _listings("<html>listing</html>")
    listed = [ListingCourse("BI101", "General Biology", detail_url="u1"),
              ListingCourse("BI102", "General Biology", detail_url="u2")]
    with patch("src.schedule.fetcher.fetch_listings_async", fake), \
            patch("src.schedule.parser.parse_listing_html", return_value=listed):
        out = await tool("find_courses", {"subjects": ["BI"]}, make_session(completed=["BI101", "BI102"]))
    assert f"All BI courses listed for {TERM_LABELS[get_current_term()]} are already completed" in out
    assert "['BI']" not in out


# ── execute_tool: string array arguments (finding 30) ──────────────────────────

@pytest.mark.parametrize("arg, expected", [
    ("CS", ["CS"]),
    ("CS, MTH", ["CS", "MTH"]),
    ('["CS", "MTH"]', ["CS", "MTH"]),
    (["cs"], ["CS"]),
])
async def test_find_courses_string_subjects_not_split_into_chars(arg, expected):
    fake, calls = _listings("")
    with patch("src.schedule.fetcher.fetch_listings_async", fake):
        await tool("find_courses", {"subjects": arg})
    assert calls == [expected]


@pytest.mark.parametrize("arg, expected", [
    ("COMM100", ["COMM100"]),
    ('["COMM100", "WR121"]', ["COMM100", "WR121"]),
    ("COMM100, WR121", ["COMM100", "WR121"]),
    (["COMM100"], ["COMM100"]),
    (None, []),
])
async def test_build_schedule_string_excluded_courses(arg, expected):
    plan = AsyncMock(return_value="plan")
    args = {"preferences": "12 credits"}
    if arg is not None:
        args["excluded_courses"] = arg
    with patch("src.agent.schedule_planner.plan_schedule_async", plan):
        assert await tool("build_schedule", args) == "plan"
    assert plan.call_args.kwargs["extra_excluded"] == expected


# ── get_pcc_page: host allow-list (finding 25) ─────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8080/admin?pcc.edu",
    "http://127.0.0.1:8080/admin#pcc.edu",
    "https://evil.example/?pcc.edu",
    "https://pcc.edu.attacker.net/x",
    "https://www.pcc.edu@127.0.0.1/",
    "https://evilpcc.edu/",
    "ftp://www.pcc.edu/file",
    "file:///etc/passwd?pcc.edu",
])
async def test_get_pcc_page_rejects_non_pcc_hosts(url):
    fetch = AsyncMock(return_value="SECRET")
    with patch("src.agent.tools._fetch_page_text", fetch):
        out = await tool("get_pcc_page", {"url": url})
    assert out == "Only pcc.edu pages are supported."
    fetch.assert_not_awaited()


@pytest.mark.parametrize("url", [
    "https://www.pcc.edu/jobs/student-employment/",
    "https://pcc.edu/",
    "http://catalog.pcc.edu/x",
])
async def test_get_pcc_page_allows_pcc_hosts(url):
    fetch = AsyncMock(return_value="page text")
    with patch("src.agent.tools._fetch_page_text", fetch):
        out = await tool("get_pcc_page", {"url": url})
    assert out == f"Content from {url}:\n\npage text"


async def test_get_pcc_page_does_not_follow_redirect_off_pcc():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == "www.pcc.edu":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:9/admin"})
        return httpx.Response(200, text="<html><body>INTERNAL SECRET</body></html>")

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    with patch("src.agent.tools.httpx.AsyncClient", client):
        out = await tool("get_pcc_page", {"url": "https://www.pcc.edu/jobs/"})
    assert "INTERNAL SECRET" not in out
    assert seen == ["www.pcc.edu"]


# ── Providers: plain-dict messages (finding 24) ────────────────────────────────

def _completion(content=None, tool_calls=None) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [
            {"id": f"call_{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
            for i, (n, a) in enumerate(tool_calls)
        ]
    return {
        "id": "chatcmpl-x", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "message": msg, "logprobs": None,
                     "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _ollama_chunk(content="", done=False, tool_calls=None) -> ChatResponse:
    calls = [Message.ToolCall(function=Message.ToolCall.Function(name=n, arguments=a)) for n, a in tool_calls or []]
    return ChatResponse(model="m", done=done,
                        message=Message(role="assistant", content=content, tool_calls=calls or None))


async def _stream(chunks):
    for chunk in chunks:
        yield chunk


async def _drain(provider, messages, session=None):
    return [t async for t in provider.stream_with_tools(messages, session or AgentSession())]


def _visible(tokens) -> str:
    return "".join(t for t in tokens if not t.startswith("\x00"))


def _assert_plain_assistant_turn(messages):
    assert all(isinstance(m, dict) for m in messages)
    json.dumps(messages)  # serializable: no SDK objects
    turn = messages[1]
    assert turn["role"] == "assistant"
    assert turn["tool_calls"][0]["function"]["name"] == "search_pcc"
    assert messages[2]["role"] == "tool"


@pytest.mark.parametrize("provider_cls, completion_cls", [
    (GroqProvider, GroqChatCompletion),
    (NineRouterProvider, OpenAIChatCompletion),
])
async def test_openai_style_providers_append_plain_dicts(provider_cls, completion_cls):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=[
        completion_cls.model_validate(_completion(tool_calls=[("search_pcc", {"query": "tuition"})])),
        completion_cls.model_validate(_completion("Tuition is $X.")),
    ])
    provider = provider_cls(client=client) if provider_cls is GroqProvider else provider_cls()
    provider._client = client
    messages = [{"role": "user", "content": "tuition?"}]
    with patch("src.agent.providers.base.execute_tool", AsyncMock(return_value="tuition data")):
        tokens = await _drain(provider, messages)
    assert _visible(tokens) == "Tuition is $X."
    _assert_plain_assistant_turn(messages)
    call = messages[1]["tool_calls"][0]
    assert call["id"] == "call_0"
    assert json.loads(call["function"]["arguments"]) == {"query": "tuition"}
    assert messages[2]["tool_call_id"] == "call_0"


async def test_ollama_appends_plain_dicts():
    client = MagicMock()
    client.chat = AsyncMock(side_effect=[
        _stream([_ollama_chunk("", True, tool_calls=[("search_pcc", {"query": "tuition"})])]),
        _stream([_ollama_chunk("Tuition info."), _ollama_chunk("", True)]),
    ])
    messages = [{"role": "user", "content": "tuition?"}]
    with patch("src.agent.providers.base.execute_tool", AsyncMock(return_value="tuition data")):
        tokens = await _drain(OllamaProvider(client=client), messages)
    assert _visible(tokens) == "Tuition info."
    _assert_plain_assistant_turn(messages)
    assert messages[1]["tool_calls"][0]["function"]["arguments"] == {"query": "tuition"}


# ── Ollama: tool calls streamed before the done chunk (finding 28) ─────────────

async def test_ollama_collects_tool_calls_from_non_final_chunks():
    client = MagicMock()
    client.chat = AsyncMock(side_effect=[
        _stream([_ollama_chunk("", False, tool_calls=[("search_pcc", {"query": "tuition"})]),
                 _ollama_chunk("", True)]),
        _stream([_ollama_chunk("Tuition is $X per credit."), _ollama_chunk("", True)]),
    ])
    with patch("src.agent.providers.base.execute_tool", AsyncMock(return_value="tuition data")) as exec_tool:
        tokens = await _drain(OllamaProvider(client=client), [{"role": "user", "content": "tuition?"}])
    exec_tool.assert_awaited_once()
    assert exec_tool.call_args.args[:2] == ("search_pcc", {"query": "tuition"})
    assert "\x00tool:search_pcc\x00" in tokens
    assert _visible(tokens) == "Tuition is $X per credit."


async def test_ollama_collects_tool_calls_spread_over_chunks():
    client = MagicMock()
    client.chat = AsyncMock(side_effect=[
        _stream([_ollama_chunk("", False, tool_calls=[("search_pcc", {"query": "a"})]),
                 _ollama_chunk("", False, tool_calls=[("get_pcc_page", {"url": "https://www.pcc.edu/"})]),
                 _ollama_chunk("", True)]),
        _stream([_ollama_chunk("Done."), _ollama_chunk("", True)]),
    ])
    with patch("src.agent.providers.base.execute_tool", AsyncMock(return_value="r")) as exec_tool:
        tokens = await _drain(OllamaProvider(client=client), [{"role": "user", "content": "q"}])
    assert [c.args[0] for c in exec_tool.call_args_list] == ["search_pcc", "get_pcc_page"]
    assert _visible(tokens) == "Done."


# ── Empty final answer never leaves a blank reply (finding 29) ─────────────────

@pytest.mark.parametrize("final", ["", "<think>only reasoning</think>", "   "])
async def test_groq_empty_final_answer_yields_apology(final):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=[
        GroqChatCompletion.model_validate(_completion(tool_calls=[("search_pcc", {"query": "housing"})])),
        GroqChatCompletion.model_validate(_completion(final)),
    ])
    with patch("src.agent.providers.base.execute_tool", AsyncMock(return_value="r")):
        tokens = await _drain(GroqProvider(client=client), [{"role": "user", "content": "housing?"}])
    assert APOLOGY in _visible(tokens)


async def test_groq_tool_use_failed_retry_with_empty_answer_yields_apology():
    from groq import BadRequestError

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=[
        BadRequestError("tool_use_failed: malformed", response=MagicMock(), body={}),
        GroqChatCompletion.model_validate(_completion("")),
    ])
    tokens = await _drain(GroqProvider(client=client), [{"role": "user", "content": "q"}])
    assert APOLOGY in _visible(tokens)


async def test_nine_router_empty_final_answer_yields_apology():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=OpenAIChatCompletion.model_validate(_completion("<think>x</think>"))
    )
    provider = NineRouterProvider()
    provider._client = client
    tokens = await _drain(provider, [{"role": "user", "content": "q"}])
    assert APOLOGY in _visible(tokens)


async def test_ollama_think_only_answer_yields_apology():
    client = MagicMock()
    client.chat = AsyncMock(return_value=_stream([_ollama_chunk("<think>hmm</think>"), _ollama_chunk("", True)]))
    tokens = await _drain(OllamaProvider(client=client), [{"role": "user", "content": "q"}])
    assert APOLOGY in _visible(tokens)
