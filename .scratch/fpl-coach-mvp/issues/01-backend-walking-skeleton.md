# 01 — Backend walking skeleton

**What to build:** The first end-to-end path through the backend: a Postgres schema minimal enough to hold a Team and its Players, a Reasoning interface (`reason(prompt, tools) -> Recommendation`, ADR-0003) with Claude as the v1 implementation, one Tool (`get_my_team`), and an endpoint that answers "who should I captain?" against seeded (manually inserted, not yet ingested) team data. The primary pick in the returned Recommendation must be identical on repeated calls against identical seeded state (ADR-0004) — framing/alternatives may vary. Verified via API tests, no UI required yet.

**Blocked by:** None — can start immediately

**Status:** done

- [x] Postgres schema exists for Players and Team, hosted on Supabase (Postgres+pgvector only, ADR-0011)
- [x] `get_my_team` tool reads seeded Team/Player data
- [x] Reasoning interface has a Claude-backed implementation and a deterministic test double
- [x] An endpoint accepts a question and returns a Recommendation with confidence, evidence, risks, and an alternative
- [x] Calling the endpoint twice against identical seeded data returns the identical primary pick both times
- [x] Seam-1 tests: FastAPI test client against a real test Postgres, seeded fixtures, asserting Recommendation shape and pick determinism, with the Reasoning interface faked via the deterministic test double
- [x] Seam-2 tests: a small number of tests, explicitly marked (e.g. `integration`), exercise the real Claude-backed implementation and are excluded from the default test run

## Comments

Schema applied to Supabase (project `fzlhslxudcfzoqkytgbk`) via MCP. `backend/.env` created using the Supavisor session pooler (`aws-1-eu-west-3.pooler.supabase.com:5432`) rather than the direct host, since this machine has no outbound IPv6. Fixed a bug in `tests/conftest.py` (`conn.executemany` doesn't exist on psycopg3 `Connection`, only on cursors). Seam-1 suite: 5 passed. Seam-2 (`integration`) tests correctly deselected — `ANTHROPIC_API_KEY` isn't set locally.
