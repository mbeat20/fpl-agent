# 24 — Durable outcome recording

**What to build:** Make outcome recording survive a missed week. Right now the accuracy history ADR-0012 exists to build is being lost in real time, and cannot be recovered after the fact.

**Blocked by:** —

**Status:** ready-for-human

## Why this is the one with a clock on it

Measured on the live database, 2026-08-27: **8 rows in `recommendation_log`, 0 with an outcome recorded.** Season is live at GW2.

`record_pending_outcomes` resolves only rows from exactly `gameweek - 1`, and only when `run_ingestion` happens to run. Its own docstring is honest about the consequence:

> If an ingestion cycle gets skipped (ADR-0002: on-demand, not scheduled) a row's window passes and its outcome stays NULL forever, which is the honest outcome given v1 doesn't keep gameweek-by-gameweek history to catch up from.

That was a reasonable v1 trade when there was no history to lose. It is not reasonable now: ingestion is manual, the season runs 38 weeks, and every skipped week silently and permanently deletes the evidence for whether this app's advice is any good. ADR-0001 defers automatic creator trust scoring to v2 *on the basis* that v1 is collecting the history — and it isn't.

There is also a diagnosed contributing cause from 2026-08-27 worth knowing before touching this: the function resolves `gameweek - 1` rows and was being passed the FPL-current gameweek, so while `is_current` was 1 it looked for GW0 and found nothing at all. That interacts with the planning-vs-current gameweek split ([[project_fpl_planning_gameweek_trap]]) — read that before changing which gameweek is passed in, because the two callers want different answers and `run_ingestion` deliberately passes `squad_gameweek` rather than the planning gameweek.

The reason the window is one gameweek wide is real and must be respected: bootstrap-static's `cost_change_event` and `event_points` are only ever *this* event's numbers, so resolving an older row against them writes the wrong gameweek's data onto it. The fix is to stop depending on bootstrap-static for historical resolution, not to widen the window.

## Approach

- Resolve outcomes from a per-gameweek source instead of the live "this event" fields. `element-summary/{id}/` carries a `history` array with per-fixture points and minutes; `/entry/{id}/history/` carries per-gameweek entry points. Either lets a row from four gameweeks ago be resolved correctly and late.
- Backfilling must be driven off "rows missing the derived field", not off what changed in this run ([[project_ingestion_backfill_pattern]]) — the same lesson as ticket 06's embedding backfill, which would otherwise have permanently skipped every pre-existing row.
- The 8 existing unresolved rows should be resolvable by this work. If they genuinely cannot be, say so in the comments rather than leaving them silently NULL.
- Ticket 12's decision to log only a plan's nearest step stands for now, but ticket 25 changes what a plan *is*; note the interaction rather than pre-empting it.
- Do not add a scheduler (ADR-0002). A CLI the user runs, that catches up on everything outstanding whenever it runs, is the right shape.
- Nothing reads `outcome_*` yet, by design. This ticket does not have to build the reporting — but it should confirm the recorded data is actually sufficient to answer "was this recommendation right", because the whole point is that v2 does not start cold.

## Acceptance criteria

- [x] An outcome can be recorded for a recommendation from several gameweeks ago, with that gameweek's real points and price movement, not the latest gameweek's
- [x] Skipping ingestion for two or more gameweeks and then running it resolves every outstanding row, verified by a test that simulates the gap
- [x] Resolution is driven by "rows lacking an outcome", never by what this run happened to touch
- [x] Creator Stance outcomes resolve on the same basis as recommendation outcomes
- [x] The 8 currently-unresolved rows are either resolved or their unresolvability is explained in the ticket comments
- [x] A row whose player cannot be found is still left NULL rather than guessed at, as today
- [x] No scheduler is introduced; catch-up happens whenever the existing CLI runs
- [x] A spot check confirms the recorded outcomes are sufficient to judge whether a past recommendation was right

## Comments

**2026-08-30 — two things changed under this ticket. Read before starting.**

**1. The "8 currently-unresolved rows" AC is moot.** Measured on the live database on 2026-08-30, `recommendation_log` holds **0 rows, 0 resolved** — not 8. Those rows are gone, cascade-deleted by the test suite's `seeded_team` teardown, which does `DELETE FROM teams WHERE id = 1` and takes every teams-cascaded table with it. Rewrite that criterion before building: there is nothing to backfill, and the deeper problem it was pointing at is now the visible one.

Which is: **building durable outcome recording while the test suite deletes the record every run is building on sand.** This ticket needs either the separate test database (raised across several sessions, still no ticket) or `recommendation_log` taken off the teams cascade. Neither is a decision to make silently inside this ticket. Note that `roadmaps` (ticket 25) has exactly the same exposure.

**2. Ticket 25 is built, and it changed what a plan is.** The note in Approach — "ticket 25 changes what a plan *is*; note the interaction rather than pre-empting it" — has now landed, so here is the interaction:

- A multi-week answer is no longer only a `TransferPlan` that renders and vanishes. It is persisted and versioned in `roadmaps`, so the *whole* plan survives, not just its nearest step.
- `_log_result` in `api/routes.py` still logs only the nearest step to `recommendation_log`, unchanged, for the reason ticket 12 gave: `record_pending_outcomes` cannot resolve future-dated rows. **That constraint is what this ticket removes.** Once it does, the natural follow-on is logging every step, or resolving steps out of the stored roadmap — the plan is now on disk either way, so the choice is open rather than forced.
- `PlanStep` now carries `player_out` as well as `pick`. An outcome for a transfer step can therefore be scored on *both* sides — what the player brought in did, and what the player sold went on to do. The second is the more interesting number and was previously unrecoverable.
- Ticket 25's own review AC is left unticked and blocked on this ticket. When it is built, that review must judge **process rather than points** — `preferences.md` is explicit that a good decision can produce a bad outcome.

---

**2026-08-30 — built and verified against live data.**

`record_pending_outcomes` is gone; `backfill_outcomes` replaces it. The mechanism changed rather than the window widening, which is what the ticket asked for: resolution now reads `player_gameweek_history` — per-gameweek `total_points` (ticket 20) plus a new per-gameweek `value` column — instead of bootstrap-static's "this event" fields. Both are facts about a specific gameweek that do not expire, so a row from four gameweeks back resolves correctly and late. Recorded as **ADR-0018**, amending ADR-0012.

**Live result of one `fpl-ingest` run: 555 outcomes recorded.** `creator_stances` went from 31 resolved to 586. The proof the ticket was actually about is in the breakdown:

| gameweek | resolved | avg points |
|---|---|---|
| 1 | 81 | 4.7 |
| 2 | 505 | 4.2 |

GW1 is **two gameweeks back**. The latest finished gameweek is 2, so under the old `gameweek - 1` window those 81 rows were permanently unresolvable — their window had passed. They are now scored on GW1's own numbers.

### Two distinctions that carry the correctness

- **"Did not feature" is a real zero; "not fetched yet" is unknown.** Identical from a missing row, and conflating them would record fake nils for players nobody ever looked up — poisoning the exact dataset this exists to build. FPL emits a history entry per fixture even at nil minutes, so the test is whether the player's history *reaches past* the gameweek.
- **A price move with no later reading is unknown, not zero.** `value` is the price when a gameweek locked, so the movement across it is the next recorded price minus that one. At a season's end there is no next price, so `outcome_price_change` is NULL while `outcome_points` still resolves.

### Two problems found while building it

**The backfill would have fanned out pool-wide.** `minutes.py` is explicit that element-summary is one call per player and must never be fanned across the pool — and a catch-up is exactly where that rule breaks by accident. Measured before running: 918 unresolved stances naming **114 distinct players** against **16** we held history for, so an unbounded run would have fired ~98 calls, and several hundred later in a season. Bounded to `_MAX_FETCHES_PER_RUN = 60`, oldest-gameweek-first, so the backlog drains across runs — the same shape ADR-0002 already chose. The live run fetched 53 and stayed inside it.

**Rows written before `value` existed would never have backfilled.** They are cached "through" the latest gameweek, so the staleness check saw nothing to do and the new column would have stayed NULL for exactly the players already on record. This is ticket 06's embedding-backfill bug in a new table ([[project_ingestion_backfill_pattern]]). `refresh_squad_minutes` now counts "missing the derived field" as stale, not just "behind the calendar".

### The AC about the 8 unresolved rows

Moot, as flagged in the comment above: `recommendation_log` is empty — those rows were cascade-deleted by the test suite before this ticket started. There was nothing to backfill. The equivalent evidence is the 586 creator stances, which exercise the identical code path (`_apply` is shared, only the table name differs). Ticked on that basis.

The underlying cause is now fixed for good: **ADR-0017** gives the suite its own database, so `recommendation_log` can accumulate instead of being deleted every run. That was the real blocker on this ticket, not the resolution logic.

### Unrelated bug found and fixed in the same session

The creator ingestion run that produced this data **crashed**, and the crash is worth recording because it destroyed work:

`record_stances` declares `"enum": STANCE_VOCABULARY`, but a tool-schema enum is a request to the model, not a guarantee from it. The extractor returned `blank` (a real FPL idea the vocabulary spells `avoid`), `_store_stances` inserted it verbatim, schema.sql's CHECK constraint rejected it, and the `CheckViolation` propagated out of `run_creator_ingestion` — killing the run after ~92 pieces of content had been fetched and paid for, and skipping the embedding pass entirely.

Two fixes: the vocabulary is enforced at the boundary where the model's output stops being trusted (dropped, never coerced to a neighbouring value — guessing that `blank` meant `avoid` would put words in a creator's mouth that ticket 19 then counts), and each item's extraction is wrapped in a **SAVEPOINT** so one failure cannot end the run. The savepoint detail is load-bearing and was caught by a test: a bare `conn.rollback()` also discarded the *content* row, losing the Grok search already paid for and leaving the in-memory counter claiming a row that no longer existed.

### Left open, deliberately

`_log_result` still records only a plan's **nearest** step — ticket 12's decision, taken because future-dated rows could not be resolved. That constraint is now lifted, so logging every step, or resolving steps out of ticket 25's stored roadmap, is newly possible. Not changed here: it is a question about what a plan commits the user to, not about how outcomes are read.

**Follow-up, same session — a non-terminating refetch loop, caught by the full suite.**

The "missing the derived field counts as stale" change had no exit condition. `_store` writes `value = EXCLUDED.value`, so a player whose element-summary supplies no price is rewritten as NULL and is stale again on the very next call — an unbounded HTTP loop, in the one module whose entire premise is that these calls are expensive and must never be fanned out.

`test_minutes_are_fetched_once_then_served_from_cache` caught it: the second call refetched instead of serving from cache. Bounded now by `fetched_at < now() - interval '1 day'`, so the real case (rows written before the column existed) is repaired on the next run, while a player FPL genuinely reports no price for costs one call a day rather than one per run forever.

Two test fixtures also encoded the old mechanism and were updated rather than worked around: `ELEMENT_SUMMARY` now carries two rounds with `value`, so `test_run_ingestion_records_outcomes_for_recommendations_from_an_earlier_gameweek` asserts **GW1's own 12 points and its own -0.1 price move** instead of GW2's `event_points`. That test now demonstrates the ticket rather than merely passing alongside it.
