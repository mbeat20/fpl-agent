# 28 — Creator attention board: who the discourse is actually on

**What to build:** A dashboard panel answering "which players are trusted creators talking about right now, and what changed" — ranked by *movement*, not by popularity.

**Blocked by:** —

**Status:** ready-for-human

## Why

Ticket 19 built the whole machine for this and then pointed it at a deliberately narrow target. `consensus.py` computes, for one player: how many distinct creators cover them, the positive/negative/neutral split, the week-over-week delta, and a `movement` classification (`surging`, `cooling`, `settled`, `new_coverage`, `dropped_off`, `steady`, `no_coverage`). `GET /team/consensus` runs it over **only** the players the user has logged an Instinct about.

That scoping was right for what ticket 19 was for — reviewing your own decision — and it means the app currently cannot answer a much more basic question: *what is everyone talking about this week?* Measured 2026-08-30, the database holds **1,605 stances across 19 creators**, and the dashboard surfaces essentially none of them unless the user has already named a player.

This is a query-scope change on existing, tested logic. `summarise_window`, `classify_movement` and `describe` all take rows and return structures; nothing about them assumes one player.

## Scope

- **Rank by the derivative, not the level.** "Salah, 12 creators" is true every week and carries no information. "1 → 7 creators in four days" is the story. The default sort is the change in creators-covering; the absolute count is shown alongside but never leads. This is the same argument `consensus.py`'s own docstring makes for why movement is the signal, applied to the ordering.
- **Count creators, never Stances.** Six posts from one account is one creator. `summarise_window` already enforces this; the board must not quietly re-count rows.
- **A recency window, not just a gameweek window.** "New in the last 48 hours" separates news from wallpaper. A theme running three weeks is background; one that appeared on Thursday is why the user opened the page. Ticket 18's Snowflake-decoded `published_at` makes this available.
- **Split by direction.** A player seven creators are trying to *sell* and a player seven are trying to *buy* are opposite facts, and `ConsensusWindow` already separates positive/negative/neutral.
- `GET /team/attention`, a read-only endpoint. **No Claude call and no live FPL API call** (ADR-0015) — this is counting rows that ingestion already wrote.
- Bounded output. The board is a top-N, and it must say what it is a slice of, the way `CreatorOpinions.total_matched` already does.

## The framing problem, which is the actual design decision

Ticket 19 scoped consensus to instinct-players **specifically so the bandwagon meter could not become a way to go shopping for bandwagons.** Its route docstring says so in as many words. This ticket deliberately inverts that scoping, so it has to carry the framing itself:

- `preferences.md` treats heavy coverage as a **risk**, not an endorsement, and ADR-0004's spirit is that the app reports and the user decides.
- So the panel is "here is what the herd is doing", never "here is what to buy". No implied ranking of quality, no "top picks" language, and the same footnote `ConsensusPanel` already carries: *a high count is as often a warning as an endorsement.*
- Once ticket 24's outcome history accumulates, the honest antidote becomes available: showing that last month's loudest name returned three points. Note the hook, do not build it here.

## Notes

- Reuse `consensus.py` wholesale. If something needs changing there, that is a signal the scoping is wrong, not that the module needs a variant.
- Gameweek attribution comes from deadlines, never from `creator_stances.gameweek` — that column is known-unreliable and `get_creator_consensus` already handles the fallback ladder (`deadlines` / `first_kickoff` / `stored_tag`). Report `gameweek_source` the same way; a board built on the stored tag means something weaker and must say so.
- Do not add a search box. A general per-player lookup is what the Ask tab is for, where the reasoning layer frames a heavy count as a risk rather than presenting it as a leaderboard.
- Watch the cost of the query. Ticket 19's per-player path is one query per player by design; a board over the whole pool must not become 600 of them.

## Acceptance criteria

- [x] A panel lists the players trusted creators are covering this gameweek, with the distinct-creator count and the positive/negative split
- [x] The default ordering is week-over-week *movement*, not absolute coverage, and the movement classification from ticket 19 is shown per row
- [x] Coverage from the last 48 hours is distinguishable from coverage carried over from earlier in the window
- [x] Creators are counted once regardless of how many times they posted
- [x] The endpoint makes no Claude call and no live FPL API call
- [x] The panel states what it is a slice of, and reports `gameweek_source` when attribution fell back to a proxy
- [x] Nothing on the panel reads as a recommendation or a ranking of quality; the "as often a warning as an endorsement" framing is present
- [x] The panel renders nothing, rather than an empty shell, when no creator content has been ingested for the gameweek
- [x] The whole board is served without one query per player

---

**2026-08-30 — built.**

`GET /creators/attention` → `AttentionBoard`, rendered by `AttentionPanel` on the dashboard. `attention.py` reuses `consensus.py` wholesale — `summarise_window`, `classify_movement` and `describe` are called unchanged, so the board and ticket 19's per-player panel cannot drift into describing the same movement two different ways.

**Ranked on the ABSOLUTE positive delta.** Ordering only on rises would have quietly made this a buy list, which is the thing ticket 19 refused to build. A player five creators have turned *against* is exactly as newsworthy as one five have turned towards, and the tests pin both: a 1→5 surge and a 5→1 collapse rank equally, and a settled 3-and-3 view ranks below both.

**One query, not one per player**, with a test that counts `FROM creator_stances` statements and asserts exactly 1. Ticket 19's per-player path is right for a handful of instinct-players and would have been hundreds of round trips here.

Not under `/team`: this is about the discourse, not the squad, and a player the user does not own is exactly the interesting row.

### The framing, which was the actual design decision

Ticket 19 scoped consensus to instinct-players *specifically* so a bandwagon meter could not become a way to go shopping for bandwagons — its route docstring says so. This ticket inverts that scoping, so it carries the framing itself: no quality ranking, no "top picks" language, and the same footnote `ConsensusPanel` already shows — *a high count is as often a warning as an endorsement*.

### Blocked follow-on

Ticket 29 was going to be built next and **cannot be, yet**. Measuring the corpus for it surfaced a serious ingestion bug — one Grok response stored as up to 51 content rows, each holding the identical blob — written up as **ticket 30**. Clustering that corpus would produce a top theme of Grok's own narration repeated across 19 creators.

Worth noting the attention board itself is mostly unaffected: `creators_covering` dedupes by creator, so the headline counts and the movement classification hold. The `stances` / "mentions" numbers are inflated by that bug and will correct themselves once ticket 30 lands.
