# Status — Richer sources and real planning depth

> **Superseded 2026-09-04 by `STATUS-2026-09-04.md`.** Read that first. §1 (what to read),
> §3 (the test database) and §7 (failures that fail open) below are still current; §5's
> "the dashboard has never been opened in a browser" is not — it has, and §3 of the newer
> file lists what that found.

**Last updated:** 2026-08-30 (late session). Previous entries 2026-08-28, 2026-08-30 (early). Read this before picking anything up.

This is the running handoff for the phase specced in `spec.md`. It says where the work is, what is verified, what is not, and what will bite you.

**If you read nothing else:** the shared-test-database problem is **solved** (§3) — running the suite is safe now, and the old "never run pytest" warning is obsolete. The one pattern still worth internalising is §7, *failures that fail open*, which caused three separate bugs in a single session.

---

## 1. Read these first, in this order

| # | File | Why |
| - | ---- | --- |
| 1 | `CONTEXT.md` (repo root) | The Language section is the vocabulary the whole codebase uses. `Creator Stance`, `Creator Consensus`, `Recommendation`, `Citation`, `Horizon`, `Preference`, `Instinct`, `Checklist Item`, `Roadmap` all have precise meanings that differ from their everyday ones. |
| 2 | `spec.md` (this directory) | The phase's goals, the user's ticket order, and a **"Verified technical findings"** section of things probed live — do not re-derive them. |
| 3 | `docs/adr/0001`–`0018` | The decisions. The four that most constrain new code: **0004** (the pick is deterministic, built from an authoritative lookup, never parsed from prose), **0014** (citations are looked up, never authored), **0011** (psycopg3 direct, *never* the Supabase SDK), **0002** (no scheduler; ingestion is an on-demand CLI). Four were added this session: **0015** dashboard reads are free, **0016** a Roadmap is persisted and versioned while a Horizon is not, **0017** the test database, **0018** outcomes resolve from per-gameweek history. |
| 4 | The ticket you are about to build | Every ticket's `## Comments` section is the real handoff for that area, and is usually longer than the ticket body. |
| 5 | `consensus.py`, `squad_rules.py`, `plan_rules.py`, `checklist.py` | The "here is the reasoning, not just the code" modules. All pure functions with the *why* in the docstrings, all deliberately separated from their DB-facing callers (`get_creator_consensus.py`, `validate_squad_move.py`, `roadmap.py`). |
| 6 | `backend/preferences.md` | ADR-0010. Plain text, appended to the system prompt. Says creator opinion is "input to weigh, never the decision itself" — that sentence is why tickets 17, 19, 28 and 29 exist, and why ticket 27's coach must not fold under repetition. |

---

## 2. Where the tickets are

| Ticket | Title | Status |
| ------ | ----- | ------ |
| 16 | Multi-platform creator sources + YouTube transcripts | **built** · 1 AC at `[~]` (needs transcripts to land) |
| 17 | Timestamped, cited evidence | **built** · 1 AC at `[~]` (same) |
| 18 | Stance recency, decay, trust tiers | **built** |
| 19 | Creator consensus / bandwagon meter | **built + integration-verified** |
| 20 | FPL state completion (bank, XI, captain, chips) | **built** · 1 AC blocked on FPL auth |
| 21 | Fixture-run tool | **built + integration-verified** |
| 22 | `validate_squad_move` enforcement | **built + integration-verified** |
| 23 | Deadline screen and checklist | **built** 2026-08-30 |
| 24 | Durable outcome recording | **built + verified on live data** 2026-08-30 |
| 25 | Persisted roadmap and plan validator | **built** 2026-08-30 · 1 AC blocked on 24's history accumulating |
| 26 | Podcast transcription | `needs-triage` — **the only ticket waiting on the user** (cost ceiling) |
| 27 | Conversational reasoning (the chat) | **built** 2026-08-30 · 2 ACs left open |
| 28 | Creator attention board | **built** 2026-08-30 |
| 29 | Common themes from embeddings | **built** 2026-08-30 · 1 AC left open (`subject` normalisation) |
| 30 | Grok blob attribution bug | **built + live data cleaned** 2026-08-30 |

Ticket 14 (website ingestion) was closed `wontfix` on 2026-08-21.

**Every ticket except 26 is built.** 26 needs a decision from the user, not work.

---

## 3. The test database — solved, 2026-08-30 (ADR-0017)

**The warning that used to be in this section — "never run pytest against the real DATABASE_URL" — is obsolete. Running the suite is safe.**

`tests/conftest.py` now **refuses to start** unless `TEST_DATABASE_URL` is set, and overwrites `os.environ["DATABASE_URL"]` with it at import. That reaches every test file, because they all read the variable at call time, as does `fpl_coach.db.get_connection`.

`TEST_DATABASE_URL` points at the **`fpl_test` schema** on the same Supabase project, as a dedicated `fpl_test_runner` role. Two properties make that a real boundary rather than a convention:

- Its `search_path` is set **server-side** (`ALTER ROLE ... SET search_path = fpl_test, public`). Putting `?options=-csearch_path=...` on the connection URL does **not** work — Supabase's Supavisor pooler silently drops it, and it fails *open*.
- The role holds **no privileges on any table in `public`**. A fall-through is a permission error, not a silent write to real data. That is the actual guarantee; the search_path is just ergonomics.

`FPL_ALLOW_LIVE_DB=1` overrides, deliberately. Pointing `TEST_DATABASE_URL` at a wholly separate project instead changes nothing else.

### Why this took five occurrences

The suite destroyed the user's squad, instincts and outcome history on 2026-08-23, 08-27 (twice) and 08-30 (twice) — the last two *after* a memory note warned about it in bold. A destructive default that relies on everyone remembering not to trigger it is a broken default. **If you find yourself writing a warning instead of a guard, write the guard.**

### Still true, deliberately

`seeded_team` still uses `MY_TEAM_ID` (1) and `test_ingestion.py` still does a bare `DELETE FROM fixtures`. Both are correct behaviour against a database that exists to be emptied.

**Adding a table means adding it to `backend/schema.sql`** — that is the only thing applied to the test schema, and `fpl-ingest` will fail with `UndefinedTable` against the live database until it is applied there too (command in `CLAUDE.md`).

---

## 4. What was built this session

Seven tickets. Each one's `## Comments` has the full account; these are the things you would otherwise have to rediscover.

**23 — deadline screen.** `GET /team/checklist`. The countdown deliberately does **not** use `planning_gameweek`, despite the ticket saying to: it resolves to `max(gameweek_state, upcoming_gameweek)`, and `upcoming_gameweek` is "earliest gameweek with an unplayed fixture" — which mid-Saturday is the gameweek *in progress*, whose deadline has passed. It runs on `gameweeks.deadline_time` instead. Checklist items carry **three** states; `unknown` stopped a half-checked XI reporting "all available", and an unscheduled fixture being reported as a blank.

**24 — durable outcomes (ADR-0018).** Replaced the mechanism rather than widening the window: resolution reads `player_gameweek_history` (per-gameweek points, plus a new `value` column) instead of bootstrap's "this event" fields. **Verified live — one `fpl-ingest` recorded 555 outcomes**, resolved stances 31 → 586, including 81 GW1 rows that were two gameweeks old and permanently unresolvable before. Bounded to `_MAX_FETCHES_PER_RUN = 60` element-summary calls, oldest-first; unbounded it would have fired ~98 in one run, which is the pool-wide fan-out `minutes.py` forbids.

**25 — roadmap + plan validator (ADR-0016).** `PlanStep` gained `player_out`, and `submit_plan` an `out_player_id` from the same recorded-id enum as the pick. Without the outgoing player a plan's arithmetic is *unknowable*, which is why ticket 12's plans went unvalidated. `roadmaps` is versioned, never overwritten, one live per team enforced by a partial unique index. The check-in stores a **fixture snapshot** at save time — without it you can say a gameweek *is* a blank but not that it *became* one, and only the second is news.

**27 — the chat.** `respond_to_user` is a third terminal in `claude.py`. **The load-bearing detail is the `must_submit` latch:** tickets 22 and 25 work by refusing a submit and handing back an `is_error`, which only holds while the submit tools are the *only* exit. After a validator rejection the prose exit is refused — otherwise the model can talk its way out of an illegal move. The transcript is server-side, gameweek-scoped, and stores **turns, not the Anthropic message list**: a rendered turn is prose and therefore cannot smuggle a player id past ADR-0004.

**28 — attention board.** `GET /creators/attention`. Reuses `consensus.py` unchanged. Ranked on the **absolute** positive delta — ordering only on rises would have quietly made it a buy list, which is exactly what ticket 19 refused to build.

**29 — themes.** `GET /creators/themes`. Cross-creator density clustering over `content_chunks.embedding`; no new dependency and **no LLM anywhere, including the labels** — each label is the cluster's medoid, a real chunk with a real `source_url`. Threshold **0.26**, measured from the corpus (nearest-neighbour p05 0.205 / median 0.289), not guessed.

**30 — the Grok blob bug.** `_BULLET_SPLIT` matched `- ` bullets; Grok returns a **numbered** list. The fallback returned one item spanning the whole response, so every cited URL was paired with the entire blob — **one response became up to 51 identical content rows**, each stance attributed to whichever URL happened to seed its row. Fixed, and the data cleaned:

| | before | after cleanup | after re-ingest |
|---|---|---|---|
| content rows | 382 | 113 | **128** |
| distinct content texts | 62 | 113 | **128** |
| chunks | 295 | 61 | **831** |
| distinct chunk texts | 62 | — | **831** |
| stances | 2,217 | 827 | 850 |

1,390 duplicate stances (63%) removed. **23 legacy blob rows deliberately survive** — they carry 36 resolved outcomes that cannot be re-fetched, so deleting them would destroy real accuracy history to tidy up prose. Ticket 29 excludes their narration *chunk* from clustering while keeping their real paraphrases.

---

## 5. What is verified and what is not

**Verified live, against the real database or API:**

- Ticket 24's backfill — 555 outcomes in one run, GW1 rows scored on GW1's own numbers.
- Ticket 30's cleanup and re-ingest — the 1:1 chunk/text ratio above.
- Ticket 29's clustering — run against the live corpus, 7 themes with real quotes and sensible player lists.
- `fpl-ingest` restores the real squad after a wipe (15 players, captain, bank, rank).

**Verified by tests only:**

- Everything in 23, 25, 27, 28, and the parser half of 30.
- **Ticket 25's plan validator has never been exercised by a real model.** No integration run has yet made Claude revise a plan against it. That is the biggest untested seam in the phase.

**Not verified:**

- **The integration suite has not been run since ticket 22.** It is excluded by default (`-m integration`). Credit is available again, so this is now just a matter of doing it.
- **The dashboard has never been opened in a browser.** `claude-in-chrome` fails to connect across sessions — verify with `curl`, do not loop retries. Eight panels now render there and none has been seen by a human.
- Ticket 27's token cost versus a single-shot exchange — an open AC needing real runs.
- **YouTube transcripts remain intermittent.** 27 video rows did land (the first ever), but the IP was blocked mid-run twice today. `YOUTUBE_PROXY_URL` is the lever.

**Suite state:** the last complete run was **409 passed / 3 failed**; all three were introduced by ticket 24's changes, all three fixed, and 53 tests across the affected suites re-verified. A confirming full run was in flight when this was written — re-run it.

---

## 6. Recommended next actions, in order

1. **Run the integration suite** (`uv run python -m pytest -m integration`). Safe now (§3), credit is available. It closes ticket 25's biggest gap — a real model revising a plan against the validator — and can clear tickets 16/17's `[~]` ACs.
2. **Walk the dashboard in a browser.** Eight panels, zero human eyes. Commands are in `CLAUDE.md`.
3. **Ticket 26**, once the user gives a cost ceiling. It hits the same YouTube IP block that stopped the transcript leg twice today.
4. **Ticket 29's open AC** (`subject` normalisation) if it becomes worth it — it is an extraction concern, and there are currently too few non-player stances for it to change what the panel shows.
5. **Ticket 30's follow-up idea:** ask Grok for structured output rather than parsing prose. The format changing underneath us is exactly what happened.

Decisions still with the user: **ticket 26's cost ceiling**, and **authenticated FPL access** — without it every affordability figure in a roadmap is an upper bound, so a plan can look affordable by 0.2m and bounce at the FPL end.

---

## 7. ⚠️ The pattern that caused three bugs in one session

Every one of these **failed open**: no error, plausible output, wrong data.

1. **Supabase's pooler silently drops `options`** on the connection URL. The `search_path` probe came back on `public`, and a `CREATE TABLE` aimed straight at real data.
2. **Ticket 24's refetch loop.** "Missing the derived field counts as stale" had no exit condition, so a player whose API response lacked `value` was refetched every run, forever — in the one module whose premise is that those calls are expensive.
3. **Ticket 30's blob attribution.** A regex that matched nothing fell back to "attribute the whole response to every URL", and 76% of the corpus quietly became duplicate narration.

The defence is the same in all three and is now applied in each: **when a parse or a lookup does not recognise what it was given, say so and degrade coarsely** — never fall back to something that looks like an answer. Ticket 29's `_items_by_offset` returning `None` rather than "one item covering everything" is the shape to copy.

Related discipline, from tickets 23, 24 and 29: **`unknown` is a first-class answer.** A checklist reporting `ok` because it had no data, or a price change recorded as `0.0` because nobody looked, is worse than one that says it does not know.

---

## 8. Environment notes that save time

- **`pytest.exe` and `uvicorn.exe` are blocked** by Windows Application Control (`os error 4551`). Use the module form: `uv run python -m pytest`, `uv run python -m uvicorn fpl_coach.app:app --host 127.0.0.1 --port 8000`, `uv run python -m fpl_coach.ingestion`. The frontend dev server runs on 5173.
- **A full suite run takes ~30–35 minutes** against remote Supabase. Run it in the background and keep working — but never two at once, they share the test schema.
- **Long bash heredocs silently write nothing** in this shell, and `\n`-style escapes inside them get mangled. Use the Write/Edit tools for anything long or escape-heavy.
- **A test file can pass in the full suite and fail run alone**, because `test_ingestion.py` sorts early and empties `fpl_players` first. `test_predict_price_changes.py` does exactly this. Check whether a failure is "my fake lost to real data" before chasing it.
- **Integration tests are excluded by default** (`addopts = "-m 'not integration'"`). They cost real API money — run them deliberately.
- **`backend/.env`** holds `DATABASE_URL`, `TEST_DATABASE_URL`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`, `VOYAGE_API_KEY`. Load them; **never print them.**
- The Xanita design system (dark mode, Xanita Red) in the global CLAUDE.md **does not apply here** — fpl-agent has its own dark palette in `frontend/src/index.css`.
