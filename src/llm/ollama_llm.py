from typing import AsyncIterator
from ollama import AsyncClient, Client
from loguru import logger
from src.config import settings


class OllamaLLM:
    def __init__(self) -> None:
        self._client = Client(host=settings.ollama_base_url)
        self._async_client = AsyncClient(host=settings.ollama_base_url)
        self._model = settings.ollama_llm_model

    def health_check(self) -> bool:
        try:
            self._client.list()
            return True
        except Exception as e:
            logger.error(f"Ollama health check failed: {e}")
            return False

    def chat(
        self, prompt: str, context: str = "", history: list[dict] | None = None
    ) -> str:
        messages = _build_messages(prompt, context, history)
        response = self._client.chat(model=self._model, messages=messages)
        return response.message.content

    async def chat_stream(
        self, prompt: str, context: str = "", history: list[dict] | None = None
    ) -> AsyncIterator[str]:
        messages = _build_messages(prompt, context, history)
        async for chunk in await self._async_client.chat(
            model=self._model, messages=messages, stream=True
        ):
            if chunk.message.content:
                yield chunk.message.content


_SYSTEM_RAG = (
    "You are a helpful assistant for Portland Community College (PCC), "
    "specializing in support for international students.\n"
    "Rules:\n"
    "- Answer ONLY from the provided context.\n"
    "- If the context says MyPCC session may be expired or course data is unavailable, "
    "tell the user their session is expired and they should run `python scripts/setup_session.py` to refresh.\n"
    "- If the context does not contain enough information, say so clearly "
    "and suggest contacting PCC directly at www.pcc.edu or call (971) 722-6111.\n"
    "- Always include the source URL if available in the context.\n"
    "- Respond in the same language as the user's question.\n"
    "- Be concise and accurate."
)

_SYSTEM_CHAT = (
    "You are a friendly assistant for Portland Community College (PCC), "
    "specializing in support for international students.\n"
    "Rules:\n"
    "- Respond naturally and conversationally — greet back, answer simple questions, be warm.\n"
    "- For PCC-specific questions you don't have data for, direct users to "
    "www.pcc.edu or call (971) 722-6111.\n"
    "- Respond in the same language as the user's question.\n"
    "- Keep responses short and friendly."
)


_MAX_HISTORY = 6  # keep last 3 turns (user+assistant pairs)


def _build_messages(
    prompt: str, context: str, history: list[dict] | None = None
) -> list[dict]:
    if context:
        system = _SYSTEM_RAG
        user_content = f"Context:\n{context}\n\nQuestion: {prompt}"
    else:
        system = _SYSTEM_CHAT
        user_content = prompt

    messages: list[dict] = [{"role": "system", "content": system}]
    if history:
        messages.extend(history[-_MAX_HISTORY:])
    messages.append({"role": "user", "content": user_content})
    return messages
