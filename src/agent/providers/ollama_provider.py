"""Ollama LLM provider — buffers content until tool decision is known, then yields word-by-word."""
import re
from typing import AsyncIterator

from loguru import logger
from ollama import AsyncClient, Message

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

from src.agent.providers.base import LLMProvider
from src.agent.session import AgentSession
from src.config import settings

# Tools whose raw output should bypass the LLM and be streamed directly.
_PASSTHROUGH_TOOLS = frozenset({"build_schedule", "get_my_courses"})


class OllamaProvider(LLMProvider):
    """Local Ollama provider, used as final fallback. Streams tool decisions."""

    def __init__(self, client: AsyncClient | None = None) -> None:
        self._client = client or AsyncClient(host=settings.ollama_base_url)

    async def stream_with_tools(
        self,
        messages: list[dict],
        session: AgentSession,
        max_iterations: int = 6,
        timeout: float = 30.0,
    ) -> AsyncIterator[str]:
        from src.agent.tools import TOOL_DEFS

        for iteration in range(max_iterations):
            collected_content = ""
            final_message: Message | None = None

            async for chunk in await self._client.chat(
                model=settings.ollama_llm_model,
                messages=messages,
                tools=TOOL_DEFS,
                stream=True,
                options={"temperature": 0.3, "num_predict": 2048},
            ):
                content = chunk.message.content or ""
                if content:
                    collected_content += content
                if chunk.done:
                    final_message = chunk.message

            if final_message is None:
                yield "\n\nError: no response from model."
                return

            tool_calls = final_message.tool_calls
            if not tool_calls:
                # No tool call — strip think tags and yield word-by-word
                clean = _THINK_RE.sub("", collected_content).strip()
                words = clean.split(" ")
                for i, word in enumerate(words):
                    yield word + (" " if i < len(words) - 1 else "")
                return

            messages.append(final_message)
            logger.info(f"Iter {iteration + 1}: {[c.function.name for c in tool_calls]}")

            for call in tool_calls:
                yield f"\x00tool:{call.function.name}\x00"
                result = await self._execute_tool_with_retry(
                    call.function.name, dict(call.function.arguments), session
                )

                if call.function.name in _PASSTHROUGH_TOOLS:
                    yield result
                    return

                messages.append({"role": "tool", "content": result})
                logger.debug(f"  ↳ {call.function.name} → {str(result)[:120]}...")

        yield "\n\nI wasn't able to complete that request. Please try rephrasing your question."
