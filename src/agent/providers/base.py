"""Abstract base class for LLM providers with tool-calling support."""
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from src.agent.session import AgentSession
from src.agent.tools import execute_tool


class LLMProvider(ABC):
    """Abstract base for LLM providers supporting tool-calling and streaming."""

    @abstractmethod
    async def stream_with_tools(
        self,
        messages: list[dict],
        session: AgentSession,
        max_iterations: int = 6,
        timeout: float = 30.0,
    ) -> AsyncIterator[str]:
        """
        Stream response with automatic tool-calling and execution.

        Yields:
        - Tool signal: `\x00tool:TOOLNAME\x00` (not rendered by UI)
        - Text: Regular response text, word by word
        - Special case: Some tools (build_schedule, get_my_courses) yield raw result directly

        Args:
            messages: Conversation message list (system + history + current user message)
            session: AgentSession containing personal context and tool state
            max_iterations: Max tool-calling iterations (default 6)
            timeout: Timeout in seconds for LLM calls (default 30)

        Raises:
            Exception: If provider fails critically. Loop will catch and try fallback.
        """
        pass

    async def _execute_tool_with_retry(
        self,
        tool_name: str,
        tool_args: dict,
        session: AgentSession,
        max_retries: int = 1,
    ) -> str:
        """Shared tool execution logic (all providers use same execute_tool)."""
        return await execute_tool(tool_name, tool_args, session)
