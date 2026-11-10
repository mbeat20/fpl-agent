# FPL Coach

A personal AI assistant for Fantasy Premier League. It recommends captaincy, transfers and chip timing for one FPL team, and every recommendation carries a confidence, the evidence behind it, the risks, and the alternatives it considered.

The evidence comes from three places:

- **Official FPL data** — your squad, bank, chips, fixtures, prices and per-gameweek history.
- **Statistics** — form, xG and fixture runs, derived from that data.
- **Trusted creators** — FPL content creators on X and YouTube, whose posts and video transcripts are read, turned into individual *Creator Stances*, and weighed by a trust tier you assign. When a recommendation leans on a creator, it links to where they said it (to the second, for a video).

A Stance is evidence, never a pick: the model weighs creators' views, but the decision is its own. See [`CONTEXT.md`](CONTEXT.md) for the full vocabulary (Recommendation, Creator Consensus, Horizon, Instinct, Roadmap, …).

## What's in the app

Five pages:

| Page | What it shows |
| ---- | ------------- |
| **Deadline** `/` | Countdown to the next deadline, a checklist of facts about the squad as it stands (captain set, flagged players in the XI, bench order, bank), price-change risk, and this week's unprompted suggestion. |
| **Squad** `/squad` | The current 15, what creators are saying about each, and your logged Instincts. |
| **Coach** `/coach` | Ask a question — "who should I captain?", "plan my next four transfers" — and get a cited Recommendation. |
| **Plan** `/plan` | The multi-week Roadmap you're following, versioned so drift from last week's plan is visible. |
| **Discourse** `/discourse` | What creators are talking about: consensus movement per player and emergent themes. |

A refresh control in the header pulls new data without a terminal.

## Architecture

```
frontend/   Vite + React + TypeScript — proxies /api to the backend on :8000
backend/    FastAPI + Python 3.12 (uv)
  ├─ ingestion/   FPL API, X (via Grok), YouTube transcripts → Postgres + pgvector
  ├─ reasoning/   Claude, with tools over the database (squad, fixtures, creator opinions, …)
  └─ tools/       the deterministic functions the model calls
docs/adr/   architecture decisions — read these before changing anything structural
.scratch/   tickets and status notes (the local issue tracker)
```

Storage is a Supabase Postgres database with pgvector, used as plain Postgres only ([ADR-0011](docs/adr/0011-supabase-postgres-only.md)).

## Running it locally

### Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node 20+
- A Postgres database with the `vector` extension (Supabase works)
- API keys: Anthropic (reasoning), xAI (X posts), Voyage AI (embeddings)

### Backend

```bash
cd backend
cp .env.example .env        # then fill it in — see below
uv sync

# create the tables
uv run python -c "import os,psycopg;from pathlib import Path;from dotenv import load_dotenv;load_dotenv();psycopg.connect(os.environ['DATABASE_URL']).execute(Path('schema.sql').read_text(encoding='utf-8'))"

uv run python -m uvicorn fpl_coach.app:app --host 127.0.0.1 --port 8000
```

`.env` settings:

| Variable | Needed for |
| -------- | ---------- |
| `DATABASE_URL` | everything |
| `FPL_ENTRY_ID` | your team — the number in your FPL team URL |
| `ANTHROPIC_API_KEY` | recommendations |
| `XAI_API_KEY` | ingesting creators' X posts |
| `VOYAGE_API_KEY` | embedding creator content for semantic search |
| `REASONING_IMPL` | `claude` (default) or `fake` for a deterministic offline run |
| `REASONING_MODEL`, `REASONING_EFFORT` | which Claude model reasons, and how hard |
| `TEST_DATABASE_URL` | running the tests (see below) |

**Preferences.** Standing rules about how you play ("I don't take hits for one-week punts") go in `backend/preferences.md`, which is loaded into every reasoning call. It's personal, so it isn't committed — create it yourself; the app runs fine without it ([ADR-0010](docs/adr/0010-preferences-as-plain-text-not-a-memory-system.md)).

> On Windows machines where App Control blocks `uvicorn.exe` / `pytest.exe`, the `python -m …` forms above and below are the ones that work.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open the URL Vite prints. API calls are proxied to the backend on port 8000.

## Getting data in

Ingestion is on demand ([ADR-0002](docs/adr/0002-on-demand-ingestion-for-v1.md)). Use the refresh control in the app's header, or the CLI — both go through the same code and are recorded in the same run ledger ([ADR-0019](docs/adr/0019-a-refresh-is-recorded-whoever-started-it.md)).

```bash
cd backend
uv run fpl-ingest                                   # FPL data — free, public APIs
uv run fpl-add-creator "Name" @handle high          # trust tier: high | medium | low ("-" for YouTube-only)
uv run fpl-link-youtube "Name" @youtube_handle
uv run fpl-ingest-creators                          # creator content — billed (Grok, Claude, Voyage)
```

FPL refreshes are free. Creator refreshes cost money on every run, so a second concurrent run of the same kind is refused rather than billed twice. YouTube transcript fetching is rate-limited by IP ([ADR-0013](docs/adr/0013-creator-platform-sources-and-transcript-rate-limits.md)).

## Tests

```bash
cd backend
uv run python -m pytest -q                  # unit + database tests
uv run python -m pytest -q -m integration   # real Claude + database calls (costs money)
```

The suite is destructive — it deletes and reseeds tables — so it **refuses to start** unless `TEST_DATABASE_URL` is set, and sends every connection there. Point it at a separate schema reached by a role with no privileges on your real tables, so a mistake is a permission error rather than lost data. [ADR-0017](docs/adr/0017-a-test-database-the-suite-cannot-escape.md) has the setup.
