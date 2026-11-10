# 19 — Creator consensus and the bandwagon meter

**What to build:** How many trusted creators are on a player right now, how that changed since last week, and how that lines up against the user's own logged Instinct. The app's answer to "am I reassessing, or am I being herded?"

**Blocked by:** 18 — Stance recency, decay, and trust tiers that mean something

**Status:** ready-for-human

## Why

Two standing rules in `preferences.md` ask for exactly this and currently have no data behind them:

> Treat creator/content opinions — including this AI's own — as input to weigh, never as the decision itself.

> Before finalizing a transfer near a deadline, check it against whatever was first considered earlier in the week; if the pick changed, be able to say why — genuine reassessment vs. bandwagon influence.

Ticket 13 built the Instinct log so the user's early-week thinking survives to deadline day. Nothing yet tells them *what changed in the world* between those two moments. With 12 creators and transcript-level coverage the count is finally meaningful: "3 of 12 were buying him on Monday, 9 of 12 are now" is a fact about the discourse, not a vibe.

## Approach

- A `get_creator_consensus(player)` tool: current-gameweek stance counts by kind, the same counts for the previous gameweek, and the delta. Built off `published_at` and the gameweek tagging ticket 18 establishes.
- The interesting output is the **derivative**, not the level. A player 11 of 12 creators have liked all season is a template pick; a player who went 2 → 10 in four days is a bandwagon forming. These must not look the same.
- Surface it against the Instinct on the dashboard: if the user logged "thinking Rogers → Enzo" on Monday and the consensus on Enzo tripled by Friday, say so plainly and let them decide. The app's job here is to make the influence visible, not to rule on it.
- Do not turn this into a scoring signal the reasoning layer optimises. Consensus is *evidence about evidence* — a high count is as often a warning as an endorsement, and the risks field is the honest place for it.

## Known-bad data this must not trust

`creator_pipeline.py` snapshots `current_gameweek` once at the start of a run, so **591 existing stances from the 2026-08-27 batch are tagged `gameweek = 1` regardless of what they are actually about** — the run began before `fpl-ingest` advanced the state. Nothing filters on the tag today, so it has been harmless; a week-over-week count is the first feature that would be silently corrupted by it.

They were deliberately not retagged, because posts spanning six days are a mix of GW1 reviews and GW2 previews and a blanket `UPDATE` would be guessing. So: derive the gameweek from `published_at` against deadline times, not from `creator_stances.gameweek`, and fix the snapshot-at-run-start bug so new rows are tagged per item rather than per run.

## Acceptance criteria

- [x] A tool returns, for a named player, current-gameweek stance counts across creators plus the previous gameweek's counts and the change
- [x] A sharp recent swing is distinguishable in the output from a consistently-held majority view
- [x] Asking about a player the user has a live Instinct on surfaces that Instinct alongside the consensus change, per ticket 13's intent
- [x] The dashboard shows consensus movement for players the user has logged an Instinct about
- [x] The reasoning layer cites consensus as evidence *and* raises heavy or fast-moving consensus as a risk, never as a bare argument for the move
- [x] Counts are computed from stored Stances with no extra API calls at question time
- [x] Gameweek attribution comes from `published_at` against deadline times, not from the known-unreliable `creator_stances.gameweek` tag
- [x] New Stances are tagged with the gameweek of the content they came from, not a single snapshot taken when the ingestion run started

## Comments

**Built 2026-08-30.** Seven of eight ACs done; AC5 is `[~]` pending a paid
integration run (the test exists and is written, it has not been executed).

### The prerequisite the ticket did not name: there was no deadline calendar

AC7 asks for gameweek attribution "from `published_at` against deadline
times". **Nothing in the database held a deadline.** `fixtures.kickoff_time`
is when the football starts, not when the manager loses the ability to act,
and the gap between them is ~90 minutes — precisely the window that decides
which gameweek a deadline-eve post belongs to.

So the first thing built was a `gameweeks` table (id, name, `deadline_time`,
finished, is_current, is_next), upserted by `run_ingestion` from
bootstrap-static's `events`. **Every field was already being fetched and
discarded**, so it costs no extra API call. Not teams-keyed — there is one
football season, not one per team — and upserted rather than
delete+reinserted, following issue 10's cascade lesson.

`gameweek.py` grew the calendar half to match: `load_deadlines`,
`gameweek_at`, `next_deadline`. **Ticket 23 will want `next_deadline` for its
countdown, and ticket 21 will want the calendar too** — neither needs to
rebuild this.

`load_deadlines` degrades in two steps rather than one, and says which it
used: real deadlines, then each gameweek's first kickoff as a proxy (so the
feature works today without re-ingesting), then nothing — at which point
consensus falls back to the stored tag and carries a `note` saying the
week-over-week change is indicative only.

### AC8: the tagging bug is fixed at the source

`creator_pipeline` took **one** planning-gameweek snapshot per run and stamped
every Stance with it. A run spanning a deadline, or pulling six days of posts,
tagged all of them the same — which is how 591 rows ended up filed under GW1
while being about GW2. Tagging is now per item, from that item's own publish
date against the calendar (`_make_tagger`). The run's planning gameweek
survives only as the fallback for content with no date at all — NULL would be
more precise and less useful, because `record_pending_outcomes` matches on
that column and an untagged Stance can never have its outcome resolved.

### Three decisions worth not re-litigating

**Creators, not Stances.** One account posting the same take six times is one
creator. This is not hypothetical — `get_creator_opinions` measured 8 of 8
returned Stances for one player coming from a single account, which is why it
has a per-creator cap. That cap stopped one voice *looking* like several in a
list; here the number **is** the output, so distinct creators is the only
honest unit. Within a window, a creator's position is their **most recent**
stance, so somebody who moved hold → buy mid-week counts once, as a buy —
and mid-week mind-changers are exactly the population this watches.

**A rise from zero coverage is not a swing.** `new_coverage` is its own
movement kind. Nobody changed their mind; the player entered the
conversation. Reporting that as a bandwagon would cry wolf on every newly
interesting differential and cost the tool its credibility for the cases that
are real. Seven movement kinds in total: `no_coverage`, `new_coverage`,
`dropped_off`, `surging`, `cooling`, `settled`, `steady`.

**No score, no citations.** Consensus is evidence *about* evidence. Turning it
into a number the reasoning layer optimises would quietly make this app a
bandwagon detector that rides bandwagons. And a count is an aggregate like a
season xG total — it has no single source, so it carries no `citation_id` and
an evidence line resting on it is correctly uncited (ADR-0014).
`get_creator_opinions` remains the citable path. Nothing here returns a
`Player`, so `_record_known_players` needs no change and a Stance still cannot
become a pick (ADR-0004).

### Deliberately NOT changed: `get_creator_opinions` still reads the stored tag

Tempting, and wrong. Its use of the tag is a coarse staleness filter backed up
by a hard 30-day age floor, so a mis-tag costs little there. Consensus is the
first feature where the tag **is** the measurement, which is what the ticket
itself says. Switching ticket 18's ranking to derived attribution would also
have silently changed what its 15 tests measure. Left alone, documented in
`consensus.py`.

### The dashboard

`GET /team/consensus` returns consensus for the players named in this
gameweek's live Instincts — matched by regex against the ingested pool
(`instincts.players_named_in`), not by asking Claude, so the dashboard stays
free of billed calls. Scoped to those players **deliberately**: the dashboard
is where the user reviews their own decision, and a general lookup box would
turn the bandwagon meter into a way to go shopping for bandwagons.

`ConsensusPanel` is built around the change — both weeks and a signed delta
always on screen together, because a settled majority and a four-day surge
produce the same current count. "Bandwagon forming" is styled as a flag to
look harder, not a red light. It reports and stops; `preferences.md` is
explicit that the call stays the user's.

### New blast radius on the shared test database

`gameweeks` is global, so `test_ingestion`'s fake bootstrap **overwrites the
real deadlines for whichever gameweek ids it names** (1, 2, 3). Its `_cleanup`
now deletes them, which leaves the calendar short rather than wrong, and
`uv run fpl-ingest` restores it — the same standing rule that already applies
to `fixtures`, which that module also wipes unscoped. **Add `gameweeks` to the
list of tables a suite run costs you.** This is the fourth disguise of the
shared-test-database problem; the separate test database is still unbuilt.

### Verification

- `test_consensus.py`: 27 tests, all passing. Covers the prolific-creator
  collapse, the mind-changer, settled-vs-surging on **identical current
  counts**, new coverage, cooling, silence-is-not-agreement, attribution from
  publish date **with deliberately wrong stored tags on every row**, undated
  stances counted rather than dropped, the Instinct join (including that a
  previous gameweek's Instinct does not surface), no-HTTP-at-question-time,
  no citation ids, the movement classifier as a table, and the calendar
  functions.
- `test_ingestion.py` gained a calendar test: deadlines land, `is_current`/
  `is_next` land, and a second run upserts rather than duplicating.
- Its own test calendar sits 400+ days out so it can never interleave with
  the real 38 rows in the shared database — an interleaved test deadline
  would let a real gameweek win the "first deadline after this date" race and
  silently change what the tests measure.
- 46 pre-existing tests across creator pipeline, instincts, preferences and
  reasoning: green.

### Open

- **AC5 needs one paid run.** `test_recommendations_integration.py` gained
  `test_claude_raises_a_fast_moving_consensus_as_a_risk_not_as_the_argument`:
  nine creators positive this gameweek against two last, and the assertion is
  on `risks` specifically — a model that only quoted the number in its
  evidence would have used popularity as an argument, which is the failure
  this exists to prevent. Run with `uv run python -m pytest -m integration -k
  consensus`.
- The reasoning-side guidance is in two places: the tool description (read in
  context) and a new "Creator opinion and consensus" section in
  `fpl_rules.py`.
- `CONTEXT.md` gained a **Creator Consensus** entry.

### Integration run, 2026-08-30

Run with `uv run python -m pytest -m integration`. **This ticket's integration
AC(s) passed.** The wider run needed three rounds because the integration file
had rotted while excluded from the default suite — details in ticket 22's
comments, since that is where the fixtures were repaired.

One caveat on the final confirming pass: it ended `20 passed, 3 failed`, and
one of those three is **`anthropic.BadRequestError: Your credit balance is too
low`** — the account ran out of credit mid-run. The other two
(`test_claude_summarizes_risk_across_the_whole_squad_not_just_one_player`,
`test_claude_pushes_back_on_a_transfer_the_data_does_not_support`) both
*passed* in the immediately preceding run and are assertions on free-form model
prose, so they are flaky rather than broken. Every integration test in the file
has passed at least once, but **there has been no single clean green run**, and
credits are exhausted so one could not be obtained. Say that rather than
claiming a green suite.
