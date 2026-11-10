# 18 — Stance recency, decay, and trust tiers that mean something

**What to build:** A Creator Stance stops being timeless. Reads are scoped and ranked by recency, stale stances stop competing with current ones, and the trust tier either starts affecting output or stops pretending to.

**Blocked by:** 16 — Multi-platform creator sources + YouTube transcript ingestion

**Status:** ready-for-human

## Why

Two measured problems, both of which ticket 16 makes ~55× worse rather than better:

1. **Stances never expire.** `get_creator_opinions` filters on `player_name ILIKE %s` with **no gameweek filter and no `LIMIT`**, ordered by `created_at DESC` and then re-sorted by trust tier. A GW1 "buy" and a GW2 "sell" arrive in the same list with nothing marking one as obsolete. There are already 1,342 stances from 9 days of ingestion.
2. **Trust tiers are a no-op.** ~~All 12 creators are `trust_tier = 'high'`.~~ (Stale as of 2026-08-28: the roster is 19 creators, 12 `high` / 7 `medium`, so the tier is a real signal.) `_TRUST_ORDER` sorts a list whose keys are identical, so ADR-0001's "manual trust tiers for v1" is, in practice, unimplemented.

Transcript ingestion will add on the order of 70k words/day across the roster. Without this ticket, "what do creators think about X" degenerates into an unbounded dump in which the loudest historical week wins.

## Approach

- Rank on `published_at` (added in ticket 16), not `fetched_at` and not `created_at` — those record when *we* saw it, which is not when it was said.
- Cap results, and make the cap visible in the tool result so the reasoning layer knows it is seeing a top slice rather than everything.
- Prefer decay to deletion: an older Stance should be visibly older and weigh less, not vanish. A Stance from before the current gameweek's deadline is usually obsolete for *this week's* decision but is exactly what ticket 19's week-over-week consensus needs, and what ADR-0012's outcome history is built on. **Do not delete rows.**
- Either make the tier affect ordering/weighting in a way that is observable in output, or record an ADR that tiers are deferred and remove the dead sort. A mechanism that silently does nothing is worse than an absent one.
- A creator having said nothing about a player recently is a real, useful answer ("no trusted creator has mentioned him since GW1") — surface it rather than reaching further back to fill the list.

## Acceptance criteria

- [x] `get_creator_opinions` and `search_creator_opinions` rank by `published_at` recency and return a bounded number of results
- [x] The tool result states how many Stances matched in total versus how many were returned, so a truncated view is never mistaken for the whole picture
- [x] Each returned Stance carries how old it is, and which gameweek it was published in, in a form the reasoning layer can cite
- [x] A Stance from a previous gameweek is distinguishable from a current-gameweek one in the model's input, and the output reflects that when the two conflict — covered by a test with a deliberately contradictory old/new pair
- [x] No Stance rows are deleted; ticket 19's week-over-week comparison and ADR-0012's outcome history both still have their data
- [x] Trust tier either demonstrably changes ordering/weighting, or an ADR records the deferral and the dead sort is removed
- [x] "No recent opinion on this player" is a distinct, returnable answer rather than an unbounded reach into older content


## Comments

### Built 2026-08-28

The ticket was not buildable as written. Its approach rests on ranking by
`published_at`, and `published_at` was NULL in **290 of 290** content rows:
ticket 16 added the column and a writer for the YouTube leg, but the roster's
entire corpus is `platform = 'x'`, and `GrokClient.search_creator_posts`
returns only `{url, text}` with no date field at all. Two prerequisites had to
be built first.

**Prerequisite A — the gameweek tag was wrong.** `creator_pipeline.py` tagged
each Stance using `current_gameweek` (the `gameweek_state` snapshot) instead of
`planning_gameweek`. Creator content is ingested in the days before a deadline,
which is exactly the window where FPL still reports the *finished* gameweek as
current ([[project_fpl_planning_gameweek_trap]]), so 591 deadline-eve GW2
opinions were stamped GW1. Three other call sites had the same bug:
`instincts.log_instinct`, `get_instincts`, and `outcomes.log_recommendation` —
the last of which is ticket 24's diagnosed contributing cause, since
`record_pending_outcomes` matches on that column.

**Prerequisite B — no publish dates.** X post ids are Snowflakes, so the
publish time was already sitting inside every `source_url` we had stored.
`_x_published_at` decodes it; all 290 rows backfilled with zero API calls.
That exposed how old the corpus actually is: 233 of 1,342 Stances predate the
season, 8 of them from 2022, all previously competing on equal terms with
yesterday's takes.

### What was built

**New:** `stance_ranking.py` — `stance_weight` = trust tier × exponential
recency decay, one-gameweek half-life. Tier multipliers are deliberately
narrower than a half-life, so a fresh medium-trust Stance outranks a stale
high-trust one.

**`get_creator_opinions`** takes a `team_id`, resolves the planning gameweek,
drops Stances more than one gameweek back *or* more than 30 days old, ranks the
rest by weight, caps at 8, and reports `total_matched` / `returned` /
`older_not_shown` / `planning_gameweek`. Each Stance carries `gameweek`,
`age_days`, `is_current_gameweek`, and a `recency` phrase written for the model
to read: *"9 days ago, for GW1 — already played, this predates the GW2
deadline"*. When everything on record is stale, `stances` is empty and `note`
says so explicitly, ending "treat this as an absence of current coverage, not
as agreement."

**`search_creator_opinions`** keeps embedding distance as the candidate filter
— ranking a loose query on recency alone surfaces today's least relevant post —
then orders by `similarity × stance_weight` over an over-fetched pool.

**Beyond the ACs, one addition:** a per-creator cap of 2. Recency ranking alone
let one prolific account take all 8 slots (measured: 8/8 for Salah from a single
creator), which reads as consensus and is nothing of the sort. Ticket 19 still
owns counting consensus properly; this only stops one voice impersonating
several.

### Contract changes worth knowing

`log_instinct` no longer raises when a team has never been ingested — fixtures
alone can now answer which deadline is next. `log_recommendation` and creator
Stance tagging fall back the same way instead of writing NULL.

### Data migrated

- 899 Stances retagged by fetch date, then 276 corrected again once publish
  dates existed (160 + 73 pre-season Stances moved to NULL rather than being
  swept into GW1; 43 moved GW2 → GW1). Final: 634 GW1, 475 GW2, 233 undated.
- 290 content rows dated from their Snowflake ids.
- No rows deleted.

### Tests

`tests/test_stance_recency.py`, 15 tests, including the ticket's required
contradictory old/new pair. 174 tests pass across every affected file.

### Left for later

Three tests were found asserting behaviour that only held while the database
was **empty** — they had been passing because the previous session's suite run
had wiped it. Now corrected, but they are the shared-test-database problem
showing up as false green rather than as data loss. That remains undecided and
unticketed.
