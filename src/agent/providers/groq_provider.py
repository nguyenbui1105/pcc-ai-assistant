"""Groq LLM provider — non-streaming tool decisions, word-by-word final answer."""
import json
import re
from typing import AsyncIterator

from groq import AsyncGroq, BadRequestError
from loguru import logger

from src.agent.providers.base import LLMProvider
from src.agent.session import AgentSession
from src.agent.tools import TOOL_DEFS
from src.config import settings

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# Tools whose raw output should bypass the LLM and be streamed directly.
_PASSTHROUGH_TOOLS = frozenset({"build_schedule", "get_my_courses"})


class GroqProvider(LLMProvider):
    """Groq cloud provider using non-streaming calls for reliable tool-call parsing."""

    def __init__(self, client: AsyncGroq | None = None) -> None:
        self._client = client or AsyncGroq(api_key=settings.groq_api_key)

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
                    model=settings.groq_model,
                    messages=messages,
                    tools=TOOL_DEFS,
                    stream=False,
                    **_opts,
                )
            except BadRequestError as e:
                if "tool_use_failed" in str(e):
                    # Model generated malformed tool call — retry without tools.
                    logger.warning(f"Groq tool_use_failed on iter {iteration + 1}, retrying without tools")
                    fallback = await self._client.chat.completions.create(
                        model=settings.groq_model,
                        messages=messages,
                        stream=False,
                        **_opts,
                    )
                    content = _THINK_RE.sub("", fallback.choices[0].message.content or "").strip()
                    words = content.split(" ")
                    for i, word in enumerate(words):
                        yield word + (" " if i < len(words) - 1 else "")
                    return
                raise
            except Exception as e:
                if "rate_limit_exceeded" in str(e) or "429" in str(e):
                    logger.warning(f"Groq rate limit hit: {e}")
                    yield "The AI service is temporarily rate-limited. Please wait 30 seconds and try again."
                    return
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
