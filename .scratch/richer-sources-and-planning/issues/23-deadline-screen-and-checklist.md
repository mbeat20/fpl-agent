# 23 — Deadline screen and pre-deadline checklist

**What to build:** The dashboard becomes a thing worth opening on a Friday: how long until the deadline, what is wrong with the squad right now, and a checklist that has to be clear before the deadline passes.

**Blocked by:** 20 — Complete the FPL state: bank, XI/bench, captain, chips, minutes

**Status:** ready-for-human

## Why

`TeamDashboard` currently renders one cached suggestion, the squad list, and the Instinct panel. It answers "what does the AI think", which is the question the Ask tab already answers. It does not answer the question the user actually has on deadline day: **is anything about my team broken, and how long do I have?**

Everything needed is either already in the database or arrives with ticket 20:

- The deadline itself: `fixtures.kickoff_time` is ingested, and `planning_gameweek` already resolves which gameweek is being planned. Note the trap ([[project_fpl_planning_gameweek_trap]]): FPL leaves `is_current` on a *finished* gameweek until the next kicks off, which is precisely the window in which someone plans. Use `planning_gameweek`, never bootstrap's `is_current`.
- Price-change risk on the user's own players: `predict_price_changes` exists (ticket 08) and the dashboard never calls it. A player about to fall is a decision the user has hours to make.
- Flagged players: `get_injury_news` exists (ticket 07) and the dashboard never calls it either.
- Captain set, bench order, bank, chips remaining: all arriving with ticket 20.

## Scope

- A countdown to the next deadline, prominent, derived from the planning gameweek rather than the current one.
- A checklist of things that are *checkable facts*, not opinions: captain is set, no flagged player is in the XI, bench order is sensible, bank is not stranded, no player in the XI has no fixture this gameweek (a blank — ticket 21).
- Price risk on owned players only, not the global rising/falling lists. The user cares about their own squad here.
- **Do not generate this with a Claude call.** These are deterministic reads. ADR-0002 keeps the AI suggestion on-demand and cached (ticket 15's `team_suggestions`); the checklist should be a plain endpoint that is fast and free, so opening the dashboard is not a billed event. Keep the existing suggestion card as-is alongside it.
- One judgement call to make explicitly: whether an unclear checklist item should be a nudge or a warning. Err toward stating the fact and letting the user decide — `preferences.md` is emphatic that the final call is theirs.

## Acceptance criteria

- [x] The dashboard shows time remaining until the next actionable deadline, derived from the planning gameweek and correct in the window between a gameweek finishing and the next deadline
- [x] A checklist reports captain set, flagged players in the XI, bench order, bank, and any XI player with no fixture this gameweek
- [x] Price-rise and price-fall risk is shown for the user's own players
- [x] The checklist endpoint makes no Claude call and no live FPL API call — reads only
- [x] Loading the dashboard does not trigger a billed reasoning call beyond the existing cached suggestion behaviour
- [x] Each checklist item states the fact rather than instructing the user
- [x] The dashboard still renders correctly before any ingestion has run, matching the existing "no suggestion yet" handling

## Comments

**2026-08-30 — built.**

`GET /team/checklist` → `DeadlineChecklist`, rendered by a new `DeadlinePanel` sitting first on the dashboard. Reads only: no Claude call, no live FPL API call, no new API dependency of any kind.

The rule that forced most of the design below is now **ADR-0015 — opening the dashboard is free**. ADR-0002 settled when data arrives; nothing settled what *looking* at it costs, and the dashboard had only one panel until now so the question never came up. It will come up again for ticket 25's roadmap panel, which is why it is an ADR rather than a paragraph in this ticket.

### The one judgement call the ticket asked for, and the answer

The ticket asked whether an unclear item should be a nudge or a warning, and said to err toward stating the fact. It ended up being a three-state answer rather than a two-state one, and that turned out to be the whole design:

- **`ok`** — checked, and fine.
- **`attention`** — checked, and there is something here. Deliberately not named `warning`: it means "worth your eyes", never "you are wrong", and it is styled in the warn palette rather than the danger one for the same reason.
- **`unknown`** — could not be checked. Before ingestion, and for anything resting on data FPL does not publish.

Collapsing `unknown` into either of the others is what makes a checklist lie, and it would have lied constantly here. Two places where it bit:

- **A half-checked XI cannot report "all available".** If some players have an ingested status and some do not, the item says `1 of 2 players in the XI are listed as available, but X has no ingested status, so a flag on them would not show here.` A green tick over a squad we only half-checked is the single most misleading thing this screen could show.
- **An unscheduled fixture is not a blank.** Ticket 21 deliberately keeps "no fixture" and "no fixture scheduled yet" apart, and it would have been easy to put them back together here. A club with no GW fixture *and* an unscheduled one comes back `unknown` — "that is unscheduled, not a blank" — because a postponement awaiting a slot may still land in this gameweek.

Every `detail` string is a statement, and a test asserts none of them contains an imperative aimed at the squad (`bench`, `swap`, `replace`, `you should`, `consider`). "Run fpl-ingest" is exempt on purpose: that is an instruction about the app, not about the team.

### Where the countdown actually comes from — a deviation, stated

The ticket says to derive the countdown "from the planning gameweek rather than the current one". I did not use `planning_gameweek`, and the reason is worth recording.

The instruction's *intent* is right — never trust bootstrap's `is_current`, which FPL leaves on a FINISHED gameweek until the next kicks off. But `planning_gameweek` is not a safe input either. It resolves to `max(gameweek_state, upcoming_gameweek)`, and `upcoming_gameweek` is "the earliest gameweek with an unplayed fixture" — which on a Saturday afternoon is **the gameweek in progress, whose deadline went on Friday**. Counting down to it would have shown a deadline the user had already missed.

So the countdown runs on `gameweeks.deadline_time` (ticket 19's calendar): the next deadline that has *not* passed is, definitionally, the next moment the user can still act. Both gameweeks are reported side by side, and when they differ the note says so plainly — `GW3 is under way, its deadline has passed, so that team is locked. The next deadline you can act before is GW4's.` That difference **is** the mid-gameweek state, and hiding it would let the screen imply a locked team can still be changed. That is the ticket's "correct in the window between a gameweek finishing and the next deadline" AC, and it is tested at exactly that boundary.

The countdown also reports its own `source`. When the `gameweeks` table has not been ingested, `load_deadlines` falls back to each gameweek's first kickoff — which is roughly **90 minutes LATE**. A countdown that runs long is the one failure mode on this screen that actually costs points, so it is said out loud in the note rather than left in a field nobody reads.

### Price risk had a real trap in it

`predict_price_changes` returns the top ten each way, and filtering that output to the user's squad would have been the obvious implementation and quietly wrong: a squad player heading for a fall is almost never among the ten steepest falls in the game, so the panel would have reported "no risk" nearly always. Added `price_changes_for(conn, player_ids)` alongside it — same query, same `_score`, narrowed by id and unlimited. Both go through one shared `_rank()`, so the dashboard and the model can never disagree about which way a player is moving.

### Also landed (ticket 20's data, finally on screen)

`frontend/src/types.ts` had never learned about ticket 20's fields, so none of it was reachable from the UI. `Player` now carries `squad_position`, `is_captain`, `is_vice_captain` and `rotation_risk`; `Team` carries `squad_gameweek`, `finances`, `overall_rank` and the points fields. `SquadList` uses them: the XI is split from the bench (labelled "in the order they come on", which is what makes the bench-order checklist item checkable by eye), the armband shows as a C/V badge, and a rotation-risk pill appears for a fit player averaging under 60 minutes. Above it, the caveat that makes all of it honest — `Lineup as of GW2 — FPL only publishes picks after a deadline locks, so this is not yet a confirmed team for GW3.`

### Tests

`tests/test_checklist.py`. Item builders are public pure functions over `Player` objects (same shape as `squad_rules.py`), so the prose — which is the deliverable — is asserted directly. `build_checklist` and the route are tested against a real seeded squad on team 90223.

The countdown tests were the awkward ones: `gameweeks` is a **global** table holding the live calendar, so a seeded far-future deadline can never win against the real GW3. They seed gameweeks 90-92 with deadlines in 2090 and pass an explicit `now` alongside, which puts every real deadline in the past. The two note cases that no live database can be made to produce on demand (`first_kickoff`, and a planning gameweek ahead of the next deadline) are covered by a pure `countdown_notes()` extracted for that purpose.

`test_checklist_route_is_free_to_call` encodes the billing AC directly: a reasoning double that raises on `reason()`, and an `FPLClient` replacement that raises on construction. The route returns 200 with both installed.

### Not done

- The checklist is not exposed to the reasoning layer as a tool. It is a dashboard read, and the model already reaches all of the same underlying facts through `get_my_team`, `get_injury_news`, `get_fixture_run` and `predict_price_changes`.
- Chips remaining is not a checklist item. `get_chip_status` needs bootstrap's chip catalogue, which is a live FPL API call — and the ticket's own AC forbids one on this endpoint. Making it a checklist item would need the catalogue persisted first; it is not worth a schema addition to say "you still have your wildcard".
- Formation legality is not checked, same as `squad_rules.py`: FPL's own UI enforces it at pick time, so an illegal XI cannot reach the database.
