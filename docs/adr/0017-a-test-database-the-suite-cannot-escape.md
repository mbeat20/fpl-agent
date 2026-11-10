# The test suite runs in a database it has no privilege to escape

Until 2026-08-30 `tests/conftest.py` connected to whatever `DATABASE_URL` held, which was the live Supabase project the dashboard reads from. Two fixtures made that destructive rather than merely untidy:

- `seeded_team` does `DELETE FROM teams WHERE id = MY_TEAM_ID` — **the real team id, 1** — in both setup and teardown, cascading through `players`, `gameweek_state`, `instincts`, `recommendation_log`, `team_suggestions` and now `roadmaps`. It then replaces the squad with a three-player Salah/Haaland/Saka placeholder.
- `test_ingestion.py` does a bare `DELETE FROM fixtures`, which is global reference data with no `team_id` to scope by, so the `TEST_TEAM_ID` discipline used elsewhere never protected it.

Running the suite therefore wiped the user's squad, their cached suggestion, their instincts and their outcome history. `players` and `fixtures` come back from `fpl-ingest`. `recommendation_log` does not — and that is the exact history ADR-0012 exists to accumulate and ADR-0001 defers creator trust-scoring on the strength of.

This happened **five times across four sessions**, twice after a note warning about it in bold had been written down and read. That is conclusive: discipline was never going to be the fix. A destructive default that requires everyone to remember not to trigger it is a broken default.

## The rule

**The suite refuses to start unless it has somewhere safe to run.** `conftest.py` raises at import if `TEST_DATABASE_URL` is unset, or if it is identical to `DATABASE_URL`. The escape hatch is `FPL_ALLOW_LIVE_DB=1`, which exists so that running against the live database remains *possible* but can only ever be *deliberate*.

The redirect is done by overwriting `os.environ["DATABASE_URL"]` at conftest import. That looks blunt, and it is the right blunt instrument: a dozen test files call `psycopg.connect(os.environ["DATABASE_URL"])` directly, and `fpl_coach.db.get_connection` reads the same variable at call time. One assignment covers every one of them, including the app under test, with no per-file changes and nothing to forget.

## Why a schema and a role, not a second project

The obvious implementation is a second Supabase project. That needs an account action, and there was a cheaper option that turns out to be *stronger*, so:

`TEST_DATABASE_URL` points at the same project, at a schema called `fpl_test`, connecting as a dedicated `fpl_test_runner` role.

Two properties make that a real boundary rather than a convention:

**The search_path is set server-side.** `ALTER ROLE fpl_test_runner SET search_path = fpl_test, public`. This matters because the obvious client-side approach does not work: `?options=-csearch_path%3D...` on the connection URL is **silently dropped by Supabase's Supavisor pooler**. Verified directly — the connection came back with `public, extensions` and a `CREATE TABLE IF NOT EXISTS teams` was aimed straight at `public`. It fails *open*, which is the worst way for a safety mechanism to fail, and it would have looked like it was working.

**The role holds no privileges on `public`'s tables.** `has_table_privilege('fpl_test_runner', 'public.teams', 'SELECT')` is false. Only `USAGE` on the schema is granted, which is the minimum needed to reference the `vector` type and its operators — pgvector is installed in `public` on this project, not in `extensions`.

That second property is the actual guarantee. The search_path is ergonomics: it means unqualified SQL finds the test tables. The privileges are what make a fall-through a permission error instead of a silent write to real data. A safety mechanism whose failure mode is "wrong data, no error" is not one.

Two behaviours were verified rather than assumed before committing to this:

- `CREATE TABLE IF NOT EXISTS teams` under `search_path = fpl_test, public` creates `fpl_test.teams` even though `public.teams` exists. It resolves the target schema first and checks existence *there*. Had it checked the whole path it would have skipped creation and every subsequent statement would have fallen through to real data.
- An unqualified `SELECT ... FROM teams` then reads the test table.

Pointing `TEST_DATABASE_URL` at a wholly separate project instead changes nothing else — the schema is the cheap implementation of the rule, not the rule.

## What was deliberately not changed

`seeded_team` still uses `MY_TEAM_ID` rather than a distinct test id. That was a genuine hazard while the suite ran against the live database; it is now a detail, because the only `teams` table the suite can reach is the test one. Changing it would mean threading an id override through every route test — the routes bind `MY_TEAM_ID` by closure — to prove nothing the boundary above does not already prove.

`test_ingestion.py`'s bare `DELETE FROM fixtures` is likewise left alone. It is correct behaviour against a database that exists to be emptied.

## Consequences

- **Tests get more honest, not just safer.** Several suites previously passed only because of run order: `test_predict_price_changes.py` seeds fake price risers that lose to the real ingested pool (Cherki, at 429,408 net transfers) and passed only because `test_ingestion.py` sorts earlier alphabetically and emptied `fpl_players` first. An empty test database removes that coupling.
- **A new table must reach `schema.sql`**, or it will not exist in `fpl_test` and the suite fails loudly. That is the desired direction of failure.
- **Ticket 24 is unblocked.** Durable outcome recording could not sensibly be built while the suite deleted `recommendation_log` on every run.
