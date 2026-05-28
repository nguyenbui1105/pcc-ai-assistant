# PCC AI Assistant

AI-powered assistant for Portland Community College (PCC) students, optimized for international / F-1 students.

Ask about PCC policies, view your personal academic data, get course recommendations, and build conflict-free schedules — all in a chat interface.

## Features

- **Personalized data** — reads your live MyPCC courses, financial balance, and GRAD Plan degree audit via cookie-based auth
- **Schedule builder** — finds open sections, checks prerequisites, enforces F-1 enrollment rules (≥12cr, ≥9 in-person), eliminates time conflicts
- **Course recommender** — ranks courses by how many degree requirements they fulfill
- **PCC search** — searches pcc.edu in real time for policy questions
- **Demo mode** — works without PCC credentials using a realistic sample student ("Alex")

## Architecture

Agentic tool-calling loop — the LLM decides which tools to call; tools fetch live data on demand.

```
Chainlit UI  →  Agent Loop (Groq primary / Ollama fallback)
                     │
                     └── Tool calls (up to 6 per query):
                           get_my_courses        → MyPCC session
                           get_degree_progress   → GRAD Plan audit
                           get_my_finances       → MyPCC session
                           find_courses          → live pcc.edu/schedule
                           build_schedule        → optimizer + planner
                           search_pcc            → DuckDuckGo + httpx
                           check_prerequisites   → static prereq map
                           get_pcc_page          → direct httpx fetch
```

## Tech Stack

| Layer | Implementation |
|---|---|
| LLM (primary) | Groq API — `llama-3.3-70b-versatile` |
| LLM (fallback) | Ollama — `qwen3:4b` (local) |
| Personal data | Playwright cookie-based fetch |
| Schedule data | httpx async + BeautifulSoup (live pcc.edu) |
| Public search | DuckDuckGo `site:pcc.edu` + httpx |
| UI | Chainlit |
| Config | pydantic-settings |

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — add your GROQ_API_KEY (free at console.groq.com)
```

### 3. (Optional) Set up personal data

```bash
# Opens a browser — log in to MyPCC and GRAD Plan to save cookies
python scripts/setup_session.py
```

Skip this step to run in demo mode with a sample student.

### 4. Start the chat UI

```bash
chainlit run ui/app.py
```

Open http://localhost:8000

## Project Structure

```
src/
├── config.py                    # Settings via pydantic-settings
├── agent/
│   ├── loop.py                  # Main agent loop (Groq + Ollama)
│   ├── tools.py                 # Tool definitions + execute_tool()
│   ├── session.py               # AgentSession dataclass + demo mode
│   ├── schedule_planner.py      # plan_schedule_async()
│   ├── course_recommender.py    # Ranked recommendations from degree gaps
│   ├── query_logger.py          # JSONL observability log
│   └── providers/               # LLM provider abstraction
├── mypcc/
│   ├── fetcher.py               # Playwright: MyPCC + GRAD Plan fetch
│   └── parser.py                # HTML → PersonalContext + DegreeAudit
└── schedule/
    ├── fetcher.py               # Live pcc.edu/schedule pages
    ├── parser.py                # CourseInfo + CourseSection
    ├── optimizer.py             # Conflict detection, F-1 rules
    ├── gap_analyzer.py          # Degree gaps → subject codes
    └── prereq_map.py            # Static prereq dict (~60+ AAOT courses)

ui/app.py                        # Chainlit app entry point
scripts/                         # Setup and manual test scripts
tests/                           # pytest unit tests
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `groq` | `groq` or `ollama` |
| `GROQ_API_KEY` | — | Get free at console.groq.com |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_LLM_MODEL` | `qwen3:4b` | Ollama model name |
| `PCC_BASE_URL` | `https://www.pcc.edu` | PCC website base URL |

## Running Tests

```bash
pytest tests/ -v
pytest tests/ -v --cov=src --cov-report=term-missing
```

## Deploy to Render

A `render.yaml` is included for deployment to Render.com.  
Set `GROQ_API_KEY` manually in the Render dashboard — do not commit it.

### Deployment Notes

| Feature | Free tier | Paid tier / self-hosted |
|---|---|---|
| Chat UI | Works | Works |
| PCC search & schedule | Works | Works |
| MyPCC personal data | **Demo mode only** | Requires persistent disk + cookies |
| GRAD Plan degree audit | **Demo mode only** | Requires persistent disk + cookies |

**Why personal data doesn't work on free tier:**
1. Playwright requires browser binaries (~300 MB) not available on Render free tier by default.
2. Cookie files (`data/*.json`) are gitignored and not present on the server.

**To enable personal data:**
- Use a paid Render instance with a **persistent disk** mounted at `/data/`.
- Copy your cookie files there after running `scripts/setup_session.py` locally.
- Or self-host with Docker — `playwright install chromium --with-deps` is already in `buildCommand`.

## Known Limitations

- PCC `data-seats` is boolean (open/closed), not actual seat count
- GRAD Plan JWT expires ~7 days — re-run `scripts/setup_session.py` to refresh
- `search_pcc` depends on DuckDuckGo rate limits — may return empty results under heavy load
- Schedule planner covers AAOT degree map only; other degrees may have incomplete gap analysis

## License

MIT
