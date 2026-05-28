"""Tests for src/agent/providers/ — Groq, Ollama, NineRouter providers."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = pytest.mark.anyio

from src.agent.session import AgentSession
from src.agent.providers.groq_provider import GroqProvider
from src.agent.providers.ollama_provider import OllamaProvider
from src.agent.providers.nine_router_provider import NineRouterProvider


# ── Helpers ───────────────────────────────────────────────────────────────────

async def collect(provider, messages, session):
    """Drain an async generator from stream_with_tools into a list."""
    tokens = []
    async for token in provider.stream_with_tools(messages, session):
        tokens.append(token)
    return tokens


def make_session() -> AgentSession:
    return AgentSession()


def make_messages() -> list[dict]:
    return [{"role": "user", "content": "test question"}]


def make_groq_response(content: str = "Hello world", tool_calls=None):
    """Build a minimal Groq chat completion response mock."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def make_groq_tool_call(name: str, args: dict, call_id: str = "call_abc"):
    tc = MagicMock()
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    tc.id = call_id
    return tc


async def make_ollama_stream(chunks):
    """Async generator that yields Ollama chunk mocks."""
    for chunk in chunks:
        yield chunk


def make_ollama_chunk(content: str = "", done: bool = False, tool_calls=None):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    chunk = MagicMock()
    chunk.message = msg
    chunk.done = done
    return chunk


# ── GroqProvider ──────────────────────────────────────────────────────────────

class TestGroqProvider:

    async def test_no_tool_calls_yields_text(self):
        """Direct text response yields content word by word."""
        client = MagicMock()
        client.chat = MagicMock()
        client.chat.completions = MagicMock()
        client.chat.completions.create = AsyncMock(
            return_value=make_groq_response("Hello world")
        )

        provider = GroqProvider(client=client)
        tokens = await collect(provider, make_messages(), make_session())

        full = "".join(tokens)
        assert "Hello" in full
        assert "world" in full
        assert full.strip() == "Hello world"

    async def test_think_tags_are_stripped(self):
        """<think>…</think> blocks removed from final answer."""
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            return_value=make_groq_response("<think>reasoning</think> Answer here")
        )
        provider = GroqProvider(client=client)
        tokens = await collect(provider, make_messages(), make_session())

        full = "".join(tokens)
        assert "<think>" not in full
        assert "Answer here" in full

    async def test_tool_call_non_passthrough(self):
        """Non-passthrough tool: sentinel yielded, result fed back, LLM continues."""
        tc = make_groq_tool_call("search_pcc", {"query": "tuition"})
        first_response = make_groq_response(tool_calls=[tc])
        second_response = make_groq_response("Tuition is $X per credit.")

        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=[first_response, second_response]
        )

        provider = GroqProvider(client=client)
        with patch(
            "src.agent.providers.base.execute_tool",
            new_callable=AsyncMock,
            return_value="search result text",
        ):
            tokens = await collect(provider, make_messages(), make_session())

        sentinels = [t for t in tokens if t.startswith("\x00tool:")]
        assert sentinels == ["\x00tool:search_pcc\x00"]
        full = "".join(t for t in tokens if not t.startswith("\x00"))
        assert "Tuition" in full

    async def test_passthrough_tool_build_schedule(self):
        """build_schedule result is yielded directly and loop exits."""
        tc = make_groq_tool_call("build_schedule", {})
        response_with_tool = make_groq_response(tool_calls=[tc])

        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=response_with_tool)

        provider = GroqProvider(client=client)
        with patch(
            "src.agent.providers.base.execute_tool",
            new_callable=AsyncMock,
            return_value="## Your Schedule\n- MTH111",
        ):
            tokens = await collect(provider, make_messages(), make_session())

        assert "\x00tool:build_schedule\x00" in tokens
        assert "## Your Schedule\n- MTH111" in tokens
        # LLM was only called once (no second call for final answer)
        assert client.chat.completions.create.call_count == 1

    async def test_passthrough_tool_get_my_courses(self):
        """get_my_courses result is yielded directly and loop exits."""
        tc = make_groq_tool_call("get_my_courses", {})
        response_with_tool = make_groq_response(tool_calls=[tc])

        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=response_with_tool)

        provider = GroqProvider(client=client)
        with patch(
            "src.agent.providers.base.execute_tool",
            new_callable=AsyncMock,
            return_value="Your courses: WR122, MTH111",
        ):
            tokens = await collect(provider, make_messages(), make_session())

        assert "\x00tool:get_my_courses\x00" in tokens
        assert "Your courses: WR122, MTH111" in tokens

    async def test_rate_limit_yields_friendly_message(self):
        """rate_limit_exceeded exception → user-visible retry message."""
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=Exception("rate_limit_exceeded: 429 Too Many Requests")
        )

        provider = GroqProvider(client=client)
        tokens = await collect(provider, make_messages(), make_session())

        full = "".join(tokens)
        assert "rate-limited" in full.lower()

    async def test_tool_use_failed_retries_without_tools(self):
        """BadRequestError with tool_use_failed → fallback call without tools."""
        from groq import BadRequestError

        fallback_response = make_groq_response("Fallback answer")
        client = MagicMock()
        # First call raises tool_use_failed, second (fallback) succeeds.
        client.chat.completions.create = AsyncMock(
            side_effect=[
                BadRequestError("tool_use_failed: malformed", response=MagicMock(), body={}),
                fallback_response,
            ]
        )

        provider = GroqProvider(client=client)
        tokens = await collect(provider, make_messages(), make_session())

        full = "".join(tokens)
        assert "Fallback answer" in full
        # Fallback call must omit tools (verify via call_args)
        _, second_kwargs = client.chat.completions.create.call_args_list[1]
        assert "tools" not in second_kwargs

    async def test_max_iterations_fallback_message(self):
        """Exhausting all iterations yields the fallback 'unable to complete' message."""
        tc = make_groq_tool_call("search_pcc", {"query": "q"})
        looping_response = make_groq_response(tool_calls=[tc])

        client = MagicMock()
        # Always returns a tool call — never a final answer.
        client.chat.completions.create = AsyncMock(return_value=looping_response)

        provider = GroqProvider(client=client)
        with patch(
            "src.agent.providers.base.execute_tool",
            new_callable=AsyncMock,
            return_value="some result",
        ):
            tokens = await collect(provider, make_messages(), make_session(), )

        full = "".join(tokens)
        assert "wasn't able to complete" in full

    async def test_malformed_tool_arguments_treated_as_empty(self):
        """JSON parse failure on tool arguments defaults to empty dict (no crash)."""
        tc = make_groq_tool_call("search_pcc", {})
        tc.function.arguments = "{bad json"
        response_with_tool = make_groq_response(tool_calls=[tc])
        final_response = make_groq_response("Done.")

        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=[response_with_tool, final_response]
        )

        provider = GroqProvider(client=client)
        with patch(
            "src.agent.providers.base.execute_tool",
            new_callable=AsyncMock,
            return_value="ok",
        ) as mock_exec:
            tokens = await collect(provider, make_messages(), make_session())

        # execute_tool called with empty dict, not crashed
        mock_exec.assert_called_once()
        _, kwargs = mock_exec.call_args
        # positional args: (tool_name, tool_args, session) — args is second positional
        assert mock_exec.call_args[0][1] == {}


# ── OllamaProvider ────────────────────────────────────────────────────────────

class TestOllamaProvider:

    async def test_streaming_content_yielded(self):
        """Content from stream chunks is yielded as it arrives."""
        chunks = [
            make_ollama_chunk("Hello "),
            make_ollama_chunk("world", done=True, tool_calls=None),
        ]

        client = MagicMock()
        client.chat = AsyncMock(return_value=make_ollama_stream(chunks))

        provider = OllamaProvider(client=client)
        tokens = await collect(provider, make_messages(), make_session())

        assert "Hello " in tokens
        assert "world" in tokens

    async def test_no_final_message_yields_error(self):
        """If no chunk has done=True, an error message is yielded."""
        chunks = [make_ollama_chunk("partial")]  # No done=True chunk

        client = MagicMock()
        client.chat = AsyncMock(return_value=make_ollama_stream(chunks))

        provider = OllamaProvider(client=client)
        tokens = await collect(provider, make_messages(), make_session())

        full = "".join(tokens)
        assert "Error" in full

    async def test_tool_call_non_passthrough(self):
        """Tool call: sentinel yielded, result appended, streaming continues."""
        tc = MagicMock()
        tc.function.name = "search_pcc"
        tc.function.arguments = {"query": "tuition"}

        chunks_round1 = [
            make_ollama_chunk("", done=True, tool_calls=[tc]),
        ]
        chunks_round2 = [
            make_ollama_chunk("Tuition info.", done=True, tool_calls=None),
        ]

        client = MagicMock()
        client.chat = AsyncMock(
            side_effect=[
                make_ollama_stream(chunks_round1),
                make_ollama_stream(chunks_round2),
            ]
        )

        provider = OllamaProvider(client=client)
        with patch(
            "src.agent.providers.base.execute_tool",
            new_callable=AsyncMock,
            return_value="tuition data",
        ):
            tokens = await collect(provider, make_messages(), make_session())

        sentinels = [t for t in tokens if "\x00tool:" in t]
        assert len(sentinels) == 1
        assert "search_pcc" in sentinels[0]

    async def test_passthrough_tool_build_schedule(self):
        """build_schedule result yielded directly; no second LLM call."""
        tc = MagicMock()
        tc.function.name = "build_schedule"
        tc.function.arguments = {}

        chunks = [make_ollama_chunk("", done=True, tool_calls=[tc])]
        client = MagicMock()
        client.chat = AsyncMock(return_value=make_ollama_stream(chunks))

        provider = OllamaProvider(client=client)
        with patch(
            "src.agent.providers.base.execute_tool",
            new_callable=AsyncMock,
            return_value="## Schedule",
        ):
            tokens = await collect(provider, make_messages(), make_session())

        assert "\x00tool:build_schedule\x00" in tokens
        assert "## Schedule" in tokens
        assert client.chat.call_count == 1

    async def test_max_iterations_fallback_message(self):
        """Exhausting all iterations yields fallback message."""
        tc = MagicMock()
        tc.function.name = "search_pcc"
        tc.function.arguments = {"query": "q"}

        def _looping_stream():
            return make_ollama_stream([make_ollama_chunk("", done=True, tool_calls=[tc])])

        client = MagicMock()
        client.chat = AsyncMock(side_effect=lambda **_: _looping_stream())

        provider = OllamaProvider(client=client)
        with patch(
            "src.agent.providers.base.execute_tool",
            new_callable=AsyncMock,
            return_value="result",
        ):
            tokens = await collect(provider, make_messages(), make_session())

        full = "".join(tokens)
        assert "wasn't able to complete" in full


# ── NineRouterProvider ────────────────────────────────────────────────────────

def make_openai_response(content: str = "Hello world", tool_calls=None):
    """Build a minimal OpenAI-compatible chat completion response mock."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def make_openai_tool_call(name: str, args: dict, call_id: str = "call_9r"):
    tc = MagicMock()
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    tc.id = call_id
    return tc


class TestNineRouterProvider:

    async def test_no_tool_calls_yields_text(self):
        """Direct text response yields content word by word."""
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("Nine router answer")
        )
        provider = NineRouterProvider()
        provider._client = client

        tokens = await collect(provider, make_messages(), make_session())

        full = "".join(tokens)
        assert "Nine router answer" in full

    async def test_think_tags_stripped(self):
        """<think>…</think> blocks removed from final answer."""
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("<think>internal</think> Clean answer")
        )
        provider = NineRouterProvider()
        provider._client = client

        tokens = await collect(provider, make_messages(), make_session())

        full = "".join(tokens)
        assert "<think>" not in full
        assert "Clean answer" in full

    async def test_tool_call_non_passthrough(self):
        """Non-passthrough tool: sentinel yielded, result fed back, LLM continues."""
        tc = make_openai_tool_call("search_pcc", {"query": "fees"})
        first = make_openai_response(tool_calls=[tc])
        second = make_openai_response("Fees are listed at pcc.edu.")

        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=[first, second])
        provider = NineRouterProvider()
        provider._client = client

        with patch(
            "src.agent.providers.base.execute_tool",
            new_callable=AsyncMock,
            return_value="fee info",
        ):
            tokens = await collect(provider, make_messages(), make_session())

        assert "\x00tool:search_pcc\x00" in tokens
        full = "".join(t for t in tokens if not t.startswith("\x00"))
        assert "Fees" in full

    async def test_passthrough_tool_build_schedule(self):
        """build_schedule result yielded directly; no second LLM call."""
        tc = make_openai_tool_call("build_schedule", {})
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            return_value=make_openai_response(tool_calls=[tc])
        )
        provider = NineRouterProvider()
        provider._client = client

        with patch(
            "src.agent.providers.base.execute_tool",
            new_callable=AsyncMock,
            return_value="## Schedule",
        ):
            tokens = await collect(provider, make_messages(), make_session())

        assert "\x00tool:build_schedule\x00" in tokens
        assert "## Schedule" in tokens
        assert client.chat.completions.create.call_count == 1

    async def test_connection_error_reraises(self):
        """Connection errors re-raise so loop.py can fall back to Groq."""
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=ConnectionError("Connection refused: localhost:20128")
        )
        provider = NineRouterProvider()
        provider._client = client

        with pytest.raises(ConnectionError):
            async for _ in provider.stream_with_tools(make_messages(), make_session()):
                pass

    async def test_max_iterations_fallback_message(self):
        """Exhausting all iterations yields fallback message."""
        tc = make_openai_tool_call("search_pcc", {"query": "q"})
        looping = make_openai_response(tool_calls=[tc])

        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=looping)
        provider = NineRouterProvider()
        provider._client = client

        with patch(
            "src.agent.providers.base.execute_tool",
            new_callable=AsyncMock,
            return_value="result",
        ):
            tokens = await collect(provider, make_messages(), make_session())

        assert "wasn't able to complete" in "".join(tokens)
