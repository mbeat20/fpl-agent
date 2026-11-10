## Agent skills

### Issue tracker

Local markdown under `.scratch/<feature>/` (no git remote). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Running the backend

Windows App Control blocks `pytest.exe` and `uvicorn.exe`. Use the module form:

```
cd backend && uv run python -m pytest -q
uv run python -m uvicorn fpl_coach.app:app --host 127.0.0.1 --port 8000
uv run python -m fpl_coach.ingestion          # fpl-ingest
```

`uvicorn` runs **without `--reload`** here on purpose — an orphaned reload
watcher was the cause of the port-8000 ghost process. A backend edit therefore
needs a manual restart before it takes effect.

### Refreshing data

Ingestion is still on demand (ADR-0002), but it no longer needs a terminal.
`POST /ingest/fpl` and `POST /ingest/creators` start a background run and
`GET /ingest/status` reports freshness and what is in flight; the frontend's
header control drives all three. Both paths go through `ingestion/jobs.py` and
land in the same `ingestion_runs` ledger, so a CLI run and a button press are
the same run (ADR-0019).

**`fpl` is free** (public APIs, no model calls) and **`creators` bills** Grok,
Claude and Voyage per run — one refresh of each is one row in the ledger, and
a second concurrent run of the same kind is refused with a 409 rather than
billed twice.

### Tests never touch the live database

The suite is destructive — `seeded_team` deletes team 1 and cascades; `test_ingestion.py` empties `fixtures`. It refuses to start unless `TEST_DATABASE_URL` is set, and redirects every connection to it. That points at the `fpl_test` schema, reached as a role with **no privileges on `public`'s tables**, so a mistake is a permission error rather than lost data. See `docs/adr/0017-a-test-database-the-suite-cannot-escape.md`. `FPL_ALLOW_LIVE_DB=1` overrides, deliberately.

Adding a table means adding it to `backend/schema.sql` — that is the only thing applied to the test schema, and `fpl-ingest` will fail with `UndefinedTable` against the live database until it is applied there too:

```
cd backend && uv run python -c "import os,psycopg;from pathlib import Path;from dotenv import load_dotenv;load_dotenv();psycopg.connect(os.environ['DATABASE_URL']).execute(Path('schema.sql').read_text(encoding='utf-8'))"
```
