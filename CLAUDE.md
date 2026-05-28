# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI-powered assistant for Portland Community College (PCC) students (optimized for international/F-1 students). The assistant answers questions about PCC policies, provides personalized data from MyPCC and GRAD Plan, and builds conflict-free course schedules.

## Architecture

The system is an **agentic tool-calling loop**, not a RAG pipeline. The LLM decides which tools to call; tools fetch live data on demand.

```
Chainlit UI (ui/app.py)
    │
    └─ agent_stream() ── src/agent/loop.py
            │
            ├─ Groq (primary, cloud, fast)   llama-3.3-70b-versatile
            └─ Ollama (local fallback)        qwen3:4b
                    │
                    └─ Tool calls (up to 6 iterations):
                        get_my_courses        → session.personal_context
                        get_degree_progress   → session.degree_audit
                        get_my_finances       → session.personal_context
                        find_courses          → schedule fetcher + parser (live pcc.edu)
                        build_schedule        → schedule_planner.plan_schedule_async()
                        search_pcc            → DuckDuckGo site:pcc.edu + page fetch
                        check_prerequisites   → prereq_map.PREREQS dict
                        get_pcc_page          → httpx direct page fetch
```

**Session startup** (`ui/app.py:on_start`): tries to load real cookies → on failure, falls back to a hardcoded "Alex" demo student so the UI works without PCC credentials.

**Tool signal protocol**: the agent loop yields `\x00tool:<name>\x00` sentinel tokens (not rendered) that the UI intercepts to update the step label in real time.

## Tech Stack

| Layer | Implementation |
|-------|---------------|
| LLM (primary) | Groq API — `llama-3.3-70b-versatile` |
| LLM (local fallback) | Ollama — `qwen3:4b` |
| Personal data | Playwright cookie-based fetch (MyPCC + GRAD Plan) |
| Schedule search | httpx async + BeautifulSoup (live pcc.edu schedule pages) |
| Public PCC search | DuckDuckGo `site:pcc.edu` + httpx page fetch |
| UI | Chainlit |
| Config | `pydantic-settings` (`src/config.py`) |

## Project Structure

```
src/
├── config.py               # Settings (LLM_PROVIDER, GROQ_API_KEY, etc.) via pydantic-settings
├── agent/
│   ├── loop.py             # Agent loop: Groq + Ollama streaming, tool call dispatch
│   ├── tools.py            # Tool definitions (TOOL_DEFS) + execute_tool() + implementations
│   ├── session.py          # AgentSession dataclass; create_demo_session() for no-cookie mode
│   ├── schedule_planner.py # plan_schedule_async(): gap analysis → fetch → conflict-free plan
│   ├── course_recommender.py # Ranked course recommendations from DegreeAudit gaps
│   └── query_logger.py     # Appends JSONL to data/query_logs.jsonl
├── mypcc/
│   ├── fetcher.py          # Playwright: fetch MyPCC pages + GRAD Plan with saved cookies
│   └── parser.py           # Parse HTML → PersonalContext + DegreeAudit dataclasses
├── schedule/
│   ├── fetcher.py          # httpx async: listing pages + detail pages from pcc.edu/schedule
│   ├── parser.py           # BS4 → CourseInfo (listing) + CourseSection (detail)
│   ├── optimizer.py        # build_schedule(), parse_prefs(), conflict detection, F-1 rules
│   ├── gap_analyzer.py     # DegreeAudit incomplete reqs → PCC subject codes (AAOT_SUBJECT_MAP)
│   └── prereq_map.py       # PREREQS dict: course_code → [required_codes]
└── llm/
    └── ollama_llm.py       # Legacy Ollama interface (not used by agent loop)

ui/
└── app.py                  # Chainlit: on_start loads session, on_message calls agent_stream

scripts/
├── setup_session.py        # Open browser, log in to MyPCC + GRAD Plan, save cookies
├── test_planner.py         # Manual test for schedule planner
├── test_recommender.py     # Manual test for course recommender (--mock flag available)
└── test_router.py          # Manual test for mypcc router

tests/                      # pytest: router, optimizer, prereq_map, gap_analyzer
data/
├── mypcc_cookies.json      # MyPCC session cookies
├── gradplan_cookies.json   # GRAD Plan cookies incl. X-AUTH-TOKEN JWT (~7 day expiry)
└── query_logs.jsonl        # Observability log (ts, route, latency_ms)
```

## Key Commands

```bash
# Install
pip install -r requirements.txt

# First-time personal data setup: opens browser for MyPCC + GRAD Plan login
python scripts/setup_session.py

# Start the chat UI
chainlit run ui/app.py

# Run tests
pytest tests/ -v
pytest tests/ -v --cov=src --cov-report=term-missing

# Run a single test file
pytest tests/test_optimizer.py -v

# Manual schedule/recommender testing
python scripts/test_recommender.py --mock   # no cookies needed
python scripts/test_planner.py

# View observability log
python -c "import json; [print(json.dumps(r)) for r in [json.loads(l) for l in open('data/query_logs.jsonl')]]"
```

## Environment Variables

Configured via `.env` (loaded by `pydantic-settings`):

```bash
# LLM provider: "groq" (default, cloud) or "ollama" (local)
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile

# Ollama (only needed if LLM_PROVIDER=ollama)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_LLM_MODEL=qwen3:4b

PCC_BASE_URL=https://www.pcc.edu
LOG_LEVEL=INFO
```

## Personal Data Layer

**MyPCC** (`data/mypcc_cookies.json`): fetches dashboard, my-courses, paying-for-college, student-guide pages. Parses into `PersonalContext` (name, degree, advisor, current courses, financial balance, upcoming dates, `is_international` flag).

**GRAD Plan** (`data/gradplan_cookies.json`): fetches `gradplan.pcc.edu` with `X-AUTH-TOKEN` JWT. Parses into `DegreeAudit` (degree name, GPA, credits applied/required, per-requirement status with `still_needed` field, completed course list).

Cookie refresh: run `scripts/setup_session.py`. GRAD Plan JWT expires ~7 days.

**Demo mode**: when cookies are missing/expired, `create_demo_session()` returns a realistic AAOT student ("Alex") so the UI is always usable.

## Schedule Planning

`find_courses` and `build_schedule` tools both fetch **live** data from `pcc.edu/schedule`:
- Listing pages → `CourseInfo` (course_code, title, detail_url)
- Detail pages → `CourseSection` (CRN, time, days, instructor, credits, `is_open`)

`build_schedule` calls `plan_schedule_async()` which: reads `DegreeAudit` gaps → maps to subject codes (`AAOT_SUBJECT_MAP`) → fetches live sections → runs `optimizer.build_schedule()` (conflict detection, prereq filter, F-1 enforcement ≥12cr / ≥9 in-person).

`data-seats` in PCC HTML is boolean (1=open), not actual seat count — JS renders the number client-side.

## Agent Loop Details

- Max 6 tool-call iterations per query (`_MAX_ITER = 6`)
- Groq uses **non-streaming** for tool-decision calls (avoids streaming tool-call parsing bugs in llama models), then re-streams the final text answer word-by-word
- Ollama uses streaming throughout; `<think>` tags are stripped
- History: last 10 messages (5 turns) appended to each call
- Temperature: 0.3 for both providers

## Known Constraints

- PCC schedule `data-seats` is boolean, not a count — cannot show "X seats left"
- GRAD Plan JWT expires ~7 days — run `setup_session.py` to refresh
- `search_pcc` relies on DuckDuckGo rate limits — can fail silently (returns empty results gracefully)
- Ollama `/no_think` prefix is prepended to user messages to suppress reasoning token leakage
- Subject code mapping in `gap_analyzer.py` covers AAOT only; other degrees may return incomplete gaps
