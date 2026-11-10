# Opening the dashboard is free — every panel but the suggestion card is a database read

ADR-0002 decided that ingestion is on-demand: no scheduler, nothing runs unless the user asks. That settles *when data arrives*. It does not settle *what looking at the data costs*, and until ticket 23 the dashboard had only one panel, so the question never had to be asked.

The Team dashboard is now the screen the user opens on a Friday to watch a countdown. If a panel there costs an Anthropic call, looking at your own team becomes a billed event and the natural act of refreshing the page becomes an expense. If it costs a live FPL API call, refreshing rate-limits the endpoint everything else depends on.

**So: every dashboard read is served from the database alone.** No Claude call, no live FPL API call. The single exception is the AI suggestion card, and it is an exception precisely because ticket 15 already made it one — generated on demand and cached per gameweek in `team_suggestions`, so it bills once per gameweek rather than once per page load.

This applies to `/team`, `/team/instincts`, `/team/consensus` and `/team/checklist` today, and to any panel added later.

## The cost of holding to it

The rule has teeth, and the clearest demonstration is what it excludes.

**Chips remaining is not on the checklist.** "Do I still have my wildcard?" is exactly the kind of checkable fact the deadline screen is for. But `get_chip_status` needs bootstrap's chip catalogue — FPL issues a fresh set each half-season with its own start/stop windows, and ADR-worthy or not, hardcoding that list is how an app ends up telling a user they hold a chip the game no longer has. The catalogue is a live API call, so the item is left out rather than the rule bent. Adding it means persisting the catalogue during ingestion first.

That is the right trade. A checklist that is cheap enough to open ten times on deadline day is worth more than one that is complete and costs something every time.

## An item that cannot be checked says so

The consequence of reading only what ingestion last wrote is that the data is sometimes stale and sometimes absent. A checklist that answered `ok` in those cases would be reporting a clean bill of health drawn from no data at all — and it would do it most confidently in exactly the situation where the user is least protected: before they have ever run ingestion.

So a `ChecklistItem` carries three states, not a boolean. `unknown` is a first-class outcome and names the gap: *"No injury statuses have been ingested for these players, so a flagged player would not show here."* Every item that rests on missing data returns one rather than dropping out of the list, because an absent item reads as a passed one.

The same discipline applies to partial data. An XI where some players have an ingested status and some do not cannot report "all available" — it reports how many it actually checked. This mirrors `SquadFinances.selling_prices_exact` (ticket 20): when the underlying source cannot answer, the app states the bound rather than presenting a guess as a fact.

## The countdown runs on the deadline calendar, not on the planning gameweek

Ticket 23 asked for a countdown derived from `planning_gameweek` rather than from bootstrap's `is_current`. The instruction's intent is right — FPL leaves `is_current` on a *finished* gameweek until the next kicks off, which is precisely the window in which someone plans. But `planning_gameweek` is not a safe input for a countdown either.

It resolves to `max(gameweek_state, upcoming_gameweek)`, and `upcoming_gameweek` is "the earliest gameweek with an unplayed fixture" — which on a Saturday afternoon is the gameweek *in progress*, whose deadline went on Friday. A countdown to it would count down to a deadline the user has already missed.

The next **unpassed deadline** in `gameweeks.deadline_time` is, definitionally, the next moment the user can still act before. That is what the countdown runs on. Both gameweeks are reported side by side, and when they differ the note says so: *"GW3 is under way — its deadline has passed, so that team is locked. The next deadline you can act before is GW4's."* The difference is the mid-gameweek state, and hiding it would let the screen imply a locked team can still be changed.

`load_deadlines` falls back to each gameweek's first kickoff when the `gameweeks` table has not been ingested. That proxy runs roughly 90 minutes **late**, and a countdown that runs long is the one failure mode on this screen that actually costs points — so the countdown reports its own `source` and says so in prose rather than leaving it in a field nobody reads.

## The checklist reports; it does not advise

There is now a second thing on the dashboard that says something about the squad, sitting directly above the AI suggestion card. They must not compete.

A `ChecklistItem` states a fact and stops: *"2 players in the XI are flagged: Saka (injured), Rice (suspended)."* It never says what to do about it. `preferences.md` is emphatic that the final call is the user's, and ADR-0004 already draws the line between a deterministic fact and a model's framing of it — this is the same line drawn on a different surface. The `attention` state means "worth your eyes", never "you are wrong", which is why it is not called `warning` and why it is styled in the warn palette rather than the danger one.

Advice belongs in the card below, where it arrives with confidence, evidence, risks and an alternative, and where the user can argue with it.
