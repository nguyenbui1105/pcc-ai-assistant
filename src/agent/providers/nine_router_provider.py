"""9Router LLM provider — OpenAI-compatible local router on localhost:20128."""
import json
import re
from typing import AsyncIterator

from loguru import logger
from openai import AsyncOpenAI

from src.agent.providers.base import LLMProvider
from src.agent.session import AgentSession
from src.agent.tools import TOOL_DEFS

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# Tools whose raw output should bypass the LLM and be streamed directly.
_PASSTHROUGH_TOOLS = frozenset({"build_schedule", "get_my_courses"})


class NineRouterProvider(LLMProvider):
    """
    9Router integration via OpenAI-compatible function-calling.

    9Router runs locally on http://localhost:20128 and routes to 40+ providers
    (Claude, Groq, GLM, MiniMax, Kiro, Vertex, etc.) based on model prefix.

    Model prefix examples:
      cc/claude-opus-4-6    → Claude via 9Router
      groq/llama-3.3-70b   → Groq via 9Router
      kr/claude-sonnet-4-5  → Kiro (free Claude tier)

    Uses non-streaming for tool-decision calls (same reliability strategy as
    GroqProvider). Connection errors re-raise so loop.py falls back to Groq.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:20128/v1",
        api_key: str = "default",
        model: str = "cc/claude-opus-4-6",
    ) -> None:
        self._model = model
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
        )

    async def stream_with_tools(
        self,
        messages: list[dict],
        session: AgentSession,
        max_iterations: int = 6,
        timeout: float = 30.0,
    ) -> AsyncIterator[str]:
        _opts: dict = {"temperature": 0.3, "max_tokens": 2048}

        for iteration in range(max_iterations):
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=TOOL_DEFS,
                    stream=False,
                    timeout=timeout,
                    **_opts,
                )
            except Exception as e:
                # Re-raise so loop.py can fall back to Groq.
                logger.error(f"9Router error on iter {iteration + 1}: {e}")
                raise

            choice = response.choices[0]
            tool_calls = choice.message.tool_calls

            if not tool_calls:
                content = _THINK_RE.sub("", choice.message.content or "").strip()
                if content:
                    words = content.split(" ")
                    for i, word in enumerate(words):
                        yield word + (" " if i < len(words) - 1 else "")
                return

            messages.append(choice.message)
            logger.info(f"Iter {iteration + 1}: {[tc.function.name for tc in tool_calls]}")

            for tc in tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    tool_args = {}

                yield f"\x00tool:{tool_name}\x00"
                result = await self._execute_tool_with_retry(tool_name, tool_args, session)

                if tool_name in _PASSTHROUGH_TOOLS:
                    yield result
                    return

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
                logger.debug(f"  ↳ {tool_name} → {str(result)[:120]}...")

        yield "\n\nI wasn't able to complete that request. Please try rephrasing your question."
