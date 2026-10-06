# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Chainlit chat assistant for Portland Community College (PCC) students, tuned for international/F-1 students. It answers PCC policy questions, shows personal data from MyPCC + GRAD Plan, recommends courses, and builds conflict-free schedules from the live pcc.edu schedule. Users may write in English or Vietnamese; the system prompt answers in the user's language, and several keyword tables include Vietnamese terms.

## Commands

```bash
pip install -r requirements.txt          # also needs `openai` (imported by the 9Router provider, not listed in requirements.txt)
cp .env.example .env                     # then set GROQ_API_KEY

chainlit run ui/app.py                   # http://localhost:8000
python scripts/setup_session.py          # opens a browser; log in to MyPCC, GRAD Plan, BANSS → writes data/*_cookies.json

pytest tests/ -v
pytest tests/test_optimizer.py -v                        # one file
pytest tests/test_optimizer.py::test_parse_days -v       # one test
pytest tests/ --cov=src --cov-report=term-missing

# Manual scripts that hit live pcc.edu (no assertions)
python scripts/test_recommender.py --mock
python scripts/test_planner.py
python scripts/test_planner_samples.py   # domestic vs F-1 scenarios
```

Tests make no network calls. Async tests use the anyio plugin (`pytestmark = pytest.mark.anyio`; `tests/conftest.py` pins the backend to asyncio), and provider tests mock the LLM clients. There is no linter or formatter config.

## Architecture

This is an **agentic tool-calling loop**, not RAG. The LLM picks tools, and the tools fetch live data.

```
ui/app.py (Chainlit) ── on_chat_start: load real session from cookies, else create_demo_session()
   └─ agent_stream()  src/agent/loop.py
        ├─ _build_messages(): _SYSTEM prompt + demo/real note + AcademicState.to_llm_context() + history
        └─ _build_provider_chain() → try each provider in order; on exception, fall through to the next
             src/agent/providers/{nine_router,groq,ollama}_provider.py
               └─ stream_with_tools(): up to _MAX_ITER=6 rounds → execute_tool()  (src/agent/tools.py)
```

### LLM providers (`LLM_PROVIDER`)
- `nine_router` → one `NineRouterProvider` per model in `NINE_ROUTER_MODELS` (comma-separated, OpenAI-compatible endpoint at `localhost:20128`), then Groq, then Ollama
- `groq` (default) → Groq, then Ollama
- `ollama` → Ollama only (prepends `/no_think` to the user message)

Fallback happens **only when a provider raises**. A Groq 429 yields a "rate-limited" message and returns without falling back. Groq and 9Router make **non-streaming** calls for tool decisions (streamed tool-call parsing was unreliable with llama models), then emit the final text word by word. Ollama streams and strips `<think>` tags.

Each provider file has its own copy of the iteration loop and of `_PASSTHROUGH_TOOLS = {"build_schedule", "get_my_courses"}`. Output from these tools goes **straight to the user** and the turn ends without another LLM call. To change tool-loop behavior, edit all three providers.

### Tool protocol
- Providers yield a `\x00tool:<name>\x00` sentinel before each tool runs. `ui/app.py` intercepts it to relabel the step and never renders it.
- **Adding or renaming a tool** means updating four places: the `TOOL_DEFS` entry and the `execute_tool()` match case in `src/agent/tools.py`, the TOOLS/RULES list in `_SYSTEM` in `src/agent/loop.py`, and `_TOOL_LABELS` in `ui/app.py`. `recommend_courses` currently has no label, so the UI shows "Using recommend_courses...".
- Tools return plain strings. `execute_tool` catches exceptions and returns an error string, so a failing tool never breaks the loop.

### Session (`src/agent/session.py`)
`AgentSession` holds `personal_context`, `degree_audit`, `academic_state`, `enrolled_times` (CRN → meeting time), and `history`. History keeps the last 20 messages, and stored assistant replies are cut to 800 chars. `create_demo_session()` builds the "Alex" AAOT/F-1 student and leaves `academic_state` as `None`, so the demo exercises the audit-only code paths. The demo flag changes the system prompt to tell the LLM to label personal data as demo data.

### Academic planning layer (`src/academic/`)
- `AcademicState` (`academic_state.py`) is the **canonical** degree state. It is built once in `ui/app.py` after the GRAD Plan audit loads and is injected into every system prompt as "AUTHORITATIVE — DO NOT RECOMPUTE". Its `completed_all` set is completed ∪ in-progress. When `academic_state` is present, the planner and recommender use it instead of re-deriving from the audit.
- `redundancy.RedundancyChecker`: drops courses that are completed, superseded (a higher-numbered course in the same subject is done), or saturated (the requirement is effectively met).
- `student_model.infer_profile()` + `enrich_profile_from_text()` → `StudentProfile` (STEM/CS affinity, transfer target, F-1)
- `course_knowledge.COURSE_KB` + `utility_scorer`: weighted utility score per course. Filler, support-lab and wellness courses are heavily penalized.
- `constraint_validator.validate_plan()`: hard constraints are F-1 ≥12 credits total, ≥9 in person, and no time conflicts

### Schedule pipeline (`src/agent/schedule_planner.py::plan_schedule_async`)
`parse_prefs` (natural-language prefs → `SchedulePrefs`) → `analyze_gaps` (incomplete requirements → subject codes) → fetch listings, then details, then capacity → sort by utility, with the redundancy penalty → `optimizer.build_schedule` (greedy) → validate → **repair loop** `_build_with_repair` → `_format_plan`.

The repair loop relaxes soft constraints in this order: subject cap 3 → 6 → drop blocked days → drop time window → any mode/section. Every stage is validated against the **original** prefs' hard constraints. `course_recommender.recommend_courses_async` follows a similar path, but ranks and explains courses instead of building a plan.

### Data sources
- **Public schedule** (`src/schedule/fetcher.py`, httpx): listing pages → `CourseInfo`, detail pages → `CourseSection`. The `data-seats` HTML attribute is only a boolean. Real seat and waitlist counts come from a POST to `pcc.edu/schedule/capacity/` (`fetch_capacity_async`) and are merged with `schedule_planner._merge_capacity`.
- **Personal data** (`src/mypcc/fetcher.py`, headless Playwright plus saved cookies): MyPCC pages → `PersonalContext`; GRAD Plan (JWT, expires about every 7 days, raises `SessionExpiredError`) → `DegreeAudit`. Enrolled meeting times come from the public schedule by CRN.
- **Policy search** (`search_pcc`): DuckDuckGo (`ddgs`) `site:pcc.edu`, then fetches the top page. When results are empty it falls back to `_PCC_TOPIC_URLS`.

## Cross-file invariants
- **Term keys** (`summer2026`, `fall2026`, ...) are hardcoded in `TERM_CODES`, `TERM_NAMES`, and `TERM_LABELS` in `src/schedule/fetcher.py`. Add each new term to all three. `get_current_term()` falls back to the last key when the computed term is missing.
- **Requirement names** from the GRAD Plan parser (`"Composition"`, `"Lab Requirement 2"`, ...) are dict keys in `gap_analyzer.AAOT_SUBJECT_MAP`/`REQ_LABELS`, `academic_state._REQ_DESCRIPTIONS`, and `redundancy._SINGLE_COURSE_REQS`/`_MULTI_DISCIPLINE_REQS`. Rename them everywhere at once. Only AAOT is mapped, so other degrees produce no gaps.
- **Course codes** are compared in normalized form. `prereq_map.normalize_code()` strips leading zeros (`MTH065` → `MTH65`). MyPCC course IDs look like `WR-122-12345` (subject-number-CRN).

## Legacy / stale code
- `src/mypcc/router.py`: a regex query classifier. The agent loop doesn't use it, but `tests/test_router.py` still tests it.
- `src/llm/ollama_llm.py` (only `scripts/test_llm.py` uses it). `scripts/test_query.py` and `scripts/crawl.py` import modules that no longer exist (`src.pipeline`, `src.crawler`).
- `litellm_config.yaml` + `scripts/start_proxy.py` run a LiteLLM proxy on port 4000. The `nine_router` provider defaults to 9Router on port 20128 instead (`scripts/start_9router.py`).
- `.env.example` sets an outdated `GROQ_MODEL`. The code default and `render.yaml` use `llama-3.3-70b-versatile`.

## Deployment
`render.yaml` deploys the Chainlit app to Render. Cookie files are gitignored, so a deployed instance runs in demo mode unless the cookies are placed on a persistent disk.
