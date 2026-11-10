# Outcomes resolve from per-gameweek history, not from "this event" fields

Amends ADR-0012, which decided that v1 logs outcomes so v2's accuracy scoring does not start cold. That decision stands. Its *implementation* was quietly failing.

## What was wrong

`record_pending_outcomes` resolved a row by reading bootstrap-static's `cost_change_event` and `event_points`. Both describe **only the current event**. A row from two gameweeks back, scored against them, would receive the latest gameweek's points as though they were its own — wrong data, not missing data.

The code knew this and refused, resolving only rows from exactly `gameweek - 1`. Given that source, the narrow window was correct.

The consequence was not. Ingestion is manual (ADR-0002: no scheduler), so **any skipped week let a row's window pass and its outcome stayed NULL forever.** Its own docstring said so and called it "the honest outcome given v1 doesn't keep gameweek-by-gameweek history to catch up from."

That premise expired. Ticket 20 built `player_gameweek_history` from `element-summary`, which is exactly gameweek-by-gameweek history. Meanwhile ADR-0001 defers automatic creator trust-scoring to v2 **on the basis** that v1 is accumulating this record, and it was not: measured on 2026-08-30, `recommendation_log` held zero resolved rows.

## The rule

**An outcome is resolved from a source that is indexed by gameweek, never from a field that means "latest".**

`player_gameweek_history` already carried `total_points` per gameweek. Ticket 24 adds `value` — the player's price at that gameweek, which `element-summary` supplies in tenths. Both are facts about a specific gameweek that do not expire, so a row from four gameweeks ago resolves *correctly* and late.

Three consequences follow, and each one is the point rather than a side effect:

- **Catch-up is possible.** Skip three gameweeks, run ingestion once, and all three gameweeks' rows resolve — each against its own numbers.
- **Resolution is driven by "rows lacking an outcome"**, never by what the current run touched. This is ticket 06's embedding-backfill lesson: keying off what changed in this cycle permanently skips everything that already existed, which is the same shape of bug in a different table.
- **The window disappears.** There is no longer a reason to bound resolution to recent rows, so the bound that remains is the only honest one: a gameweek that has not finished has no outcome yet, and must not be scored as a nil.

## Two distinctions that carry the correctness

**"Did not feature" is a real zero. "Not fetched yet" is unknown.** Both look identical from a missing history row, and conflating them would silently record nils for players nobody has ever looked up — poisoning the exact dataset this exists to build. FPL emits a history entry per fixture even at nil minutes, so the test is whether the player's history *reaches past* the gameweek in question. Reaching it with no row at it means they had no fixture: 0. Not reaching it means unknown: NULL, left alone.

**A price movement with no later reading is unknown, not zero.** `value` is the price at the moment that gameweek locked, so the movement *across* a gameweek is the next recorded price minus that one. At the end of a season, or before the following gameweek has been fetched, there is no next price — so `outcome_price_change` is NULL while `outcome_points` still resolves. Recording 0.0 there would assert that a price held steady when in fact nobody looked.

## What this does not do

Nothing reads `outcome_*` yet, by design — ADR-0012 said so and that is unchanged. This makes the record correct and complete enough to be worth reading later; building the reporting is a separate job, and ticket 25's roadmap review is its first customer.

Note also that this only fixes resolution. It does not fix the fact that a `recommendation_log` row is created only for a plan's *nearest* step (ticket 12's decision, taken because future-dated rows could not be resolved). That constraint is now lifted, and logging every step — or resolving steps out of the stored roadmap, which ADR-0016 keeps on disk — is newly possible. Left open rather than changed here, because it is a question about what a plan commits the user to, not about how outcomes are read.
