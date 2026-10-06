"""Ollama LLM provider — buffers content until tool decision is known, then yields word-by-word."""
from typing import AsyncIterator

from loguru import logger
from ollama import AsyncClient

from src.agent.providers.base import EMPTY_ANSWER, PASSTHROUGH_TOOLS, THINK_RE, LLMProvider, iter_words
from src.agent.session import AgentSession
from src.config import settings


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
            # Ollama >= 0.8 streams tool calls in non-final chunks, older versions
            # on the done chunk, so collect them from every chunk.
            tool_calls: list = []
            done = False

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
                if chunk.message.tool_calls:
                    tool_calls.extend(chunk.message.tool_calls)
                if chunk.done:
                    done = True

            if not done:
                yield "\n\nError: no response from model."
                return

            if not tool_calls:
                # No tool call — strip think tags and yield word-by-word
                clean = THINK_RE.sub("", collected_content).strip()
                for chunk in iter_words(clean or EMPTY_ANSWER):
                    yield chunk
                return

            messages.append({
                "role": "assistant",
                "content": collected_content,
                "tool_calls": [
                    {"function": {"name": c.function.name, "arguments": dict(c.function.arguments)}}
                    for c in tool_calls
                ],
            })
            logger.info(f"Iter {iteration + 1}: {[c.function.name for c in tool_calls]}")

            for call in tool_calls:
                yield f"\x00tool:{call.function.name}\x00"
                result = await self._execute_tool_with_retry(
                    call.function.name, dict(call.function.arguments), session
                )

                if call.function.name in PASSTHROUGH_TOOLS:
                    yield result
                    return

                messages.append({"role": "tool", "content": result})
                logger.debug(f"  ↳ {call.function.name} → {str(result)[:120]}...")

        yield "\n\nI wasn't able to complete that request. Please try rephrasing your question."
