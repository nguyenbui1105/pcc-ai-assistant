"""Agent loop: routes through LLM provider chain (9Router → Groq → Ollama)."""
from typing import AsyncIterator

from loguru import logger

from src.agent.providers.groq_provider import GroqProvider
from src.agent.providers.nine_router_provider import NineRouterProvider
from src.agent.providers.ollama_provider import OllamaProvider
from src.agent.session import AgentSession, _MAX_HISTORY
from src.config import settings

_MAX_ITER = 6

_SYSTEM = """\
You are a concise, friendly assistant for Portland Community College (PCC) students.

TOOLS — call only what the question directly needs:
- get_my_courses → "what am I taking?", "my classes this term"
- get_degree_progress → "graduation progress", "what do I still need?", "credits", "GPA", "requirements"
- get_my_finances → "balance", "how much do I owe?", "payment status"
- recommend_courses → "what should I take?", "recommend courses", "what courses do I still need?", "suggest classes", "gợi ý môn học" — uses utility scoring to rank by strategic value (transfer, STEM, prereq unlocks). PREFER this over find_courses for planning questions.
- find_courses → "find classes for subject X", "open CS sections" — only when user specifies a subject code
- build_schedule → "build me a schedule", "plan my semester", "what combo should I take for N credits"
- search_pcc → ANY question about PCC policies, services, deadlines, F-1 rules, tuition, jobs, clubs, campus resources, scholarships, counseling, tutoring — use this as a fallback for anything not covered by other tools
- check_prerequisites → "can I take X?", "prereqs for Y"
- get_pcc_page → fetch a specific pcc.edu URL for deeper info

RULES:
- Call ONLY the tool the question directly needs — never call multiple tools unless explicitly asked for a combined summary
- For "when do my classes meet?" or "what's my schedule this term?" — call get_my_courses, which already includes meeting days/times. NEVER call find_courses for already-enrolled courses.
- For follow-up questions ("where did you get this?", "explain more", "what do you mean?") — answer from conversation history, do NOT call any tool
- NEVER invent or guess course codes, CRNs, or course names — ALWAYS call find_courses or build_schedule for real data
- For ANY question you cannot answer from memory → call search_pcc (jobs, clubs, services, housing, library, tutoring, etc.)
- Always include the source URL as a markdown link when returning search results — format: [Page Title](url)
- Keep answers SHORT — 2-4 sentences for simple questions; use bullet points for lists
- Respond in the same language the student uses (English or Vietnamese)
- For F-1 students asking about schedules: mention the 12-credit / 9 in-person rule
- For build_schedule results: output the tool result VERBATIM — do not summarize, rephrase, or omit any part of it. After the verbatim output, you may add ONE brief sentence if needed. Never describe the schedule in prose before showing it.
- For build_schedule: if the student says they already took a course not reflected in their audit (e.g. "I already took COMM100"), pass it in the excluded_courses parameter so it won't appear in the plan.
- NEVER calculate or estimate tuition, fees, or any costs yourself — always call search_pcc for any financial figures. If the user asks about cost after seeing a schedule, call search_pcc with query "tuition fees per credit".
- Do not use emojis in responses.
"""


def _build_messages(question: str, session: AgentSession) -> list[dict]:
    system = _SYSTEM
    if session.is_demo:
        system += (
            "\nCONTEXT: DEMO MODE — you are showing a SAMPLE student profile (Alex), NOT the real user's data."
            " When answering using get_my_courses, get_degree_progress, or get_my_finances, start with"
            " '> 📌 **Demo data** — this is a sample profile, not your real PCC account.' on its own line."
            " Use 'the demo student' or 'in this demo' instead of 'you/your' when describing personal data."
            " For find_courses, build_schedule, search_pcc, check_prerequisites, and get_pcc_page results,"
            " respond normally without any demo notice — those return live PCC data."
        )
    else:
        system += "\nCONTEXT: Real student data loaded from MyPCC and GRAD Plan."

    messages: list[dict] = [{"role": "system", "content": system}]
    messages.extend(session.history[-_MAX_HISTORY:])
    prefix = "/no_think\n" if settings.llm_provider == "ollama" else ""
    messages.append({"role": "user", "content": f"{prefix}{question}"})
    return messages


def _build_provider_chain() -> list:
    """Build the ordered provider list based on LLM_PROVIDER setting."""
    if settings.llm_provider == "nine_router":
        # One NineRouterProvider per model — tried left→right until one succeeds.
        models = [m.strip() for m in settings.nine_router_models.split(",") if m.strip()]
        nine_router_providers = [
            NineRouterProvider(
                base_url=settings.nine_router_base_url,
                api_key=settings.nine_router_api_key,
                model=model,
            )
            for model in models
        ]
        return nine_router_providers + [GroqProvider(), OllamaProvider()]
    if settings.llm_provider == "groq":
        return [GroqProvider(), OllamaProvider()]
    # ollama
    return [OllamaProvider()]


async def agent_stream(question: str, session: AgentSession) -> AsyncIterator[str]:
    """Agentic tool-calling loop with automatic provider fallback chain."""
    messages = _build_messages(question, session)
    providers = _build_provider_chain()

    for provider in providers:
        try:
            async for chunk in provider.stream_with_tools(
                messages, session, max_iterations=_MAX_ITER
            ):
                yield chunk
            return
        except Exception as e:
            logger.warning(f"Provider {provider.__class__.__name__} failed: {e}, trying next")

    yield "\n\nAll LLM providers are currently unavailable. Please try again later."
