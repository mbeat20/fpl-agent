# 03 — Real FPL API ingestion

**What to build:** An on-demand refresh (ADR-0002 — no scheduler) that pulls the user's actual team, prices, ownership, form, fixtures, and injury status flags from the official FPL API and writes them into Postgres, replacing the manually seeded data from ticket 01. Running the refresh and then asking "who should I captain?" reflects real current gameweek data.

**Blocked by:** 01 — Backend walking skeleton

**Status:** done

- [x] An on-demand trigger (endpoint or CLI command in the backend project) runs the ingestion pipeline synchronously
- [x] The user's real team, prices, ownership, and form are pulled from the official FPL API and written to Postgres
- [x] Fixture data is ingested
- [x] Injury status flags (doubtful/injured/suspended) from the FPL API are ingested — no separate injury-news source
- [x] After running the refresh, `get_my_team` and the captain Recommendation reflect the real ingested data, not the ticket-01 seed data
- [x] Ingestion tests fake the FPL API client at its HTTP boundary with canned fixture responses and assert correctly-shaped rows are written

## Comments

CLI trigger: `uv run fpl-ingest` (needs `FPL_ENTRY_ID` in `.env` — the numeric id from the URL when viewing your team on fantasy.premierleague.com). Pulls `bootstrap-static` (players/clubs/gameweeks), the entry's current-gameweek picks, and all fixtures; replaces the team row and its 15 players in one pass, and fully replaces the `fixtures` table.

Schema: added `ownership`, `form`, `status`, `news` columns to `players` (nullable — NULL for ticket-01-style manually seeded rows) and a new `fixtures` table. Caught a real bug while doing this: putting the new columns inside the existing `CREATE TABLE IF NOT EXISTS players (...)` block is a no-op once the table already exists in Supabase — needed explicit `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements instead, applied as a follow-up migration.

`FPLClient` (`ingestion/fpl_client.py`) wraps the four endpoints needed (`bootstrap-static`, `fixtures`, `entry`, `entry/.../picks`) over an injectable `httpx.Client`, so tests fake the FPL API at the HTTP boundary via `httpx.MockTransport` with canned JSON rather than mocking the client's methods. `tests/test_ingestion.py` covers correctly-shaped rows written (players + fixtures) and that a refresh replaces prior seed data rather than merging with it. Full suite: 7 passed, 2 (Claude-integration) deselected.

Not run live against the real FPL API in this session — no `FPL_ENTRY_ID` was available. Pipeline logic is fully verified against faked HTTP + the real test Postgres; a live smoke test just needs the real entry id dropped into `.env`.
