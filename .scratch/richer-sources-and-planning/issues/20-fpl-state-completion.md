# 20 — Complete the FPL state: bank, XI/bench, captain, chips, minutes

**What to build:** Ingest the parts of the user's actual FPL state that the AI is already instructed to reason about but has never been given. Most of it is in API responses `run_ingestion` already fetches and throws away, so this costs no new API calls.

**Blocked by:** —

**Status:** ready-for-human

## Why

`fpl_rules.py` — sent as the system prompt on every single call — instructs the model to check things it has no data for:

> The replacement must be affordable from the selling price of the player out plus the money in the bank.

There is no bank column anywhere in the schema. `run_ingestion` calls `/entry/{id}/` and `/entry/{id}/event/{gw}/picks/` and keeps only the team name and the 15 element ids. The picks response carries `entry_history.bank`, `entry_history.value`, `event_transfers`, the XI/bench ordering (`position`), `is_captain`, `is_vice_captain`, and `active_chip`. All discarded ([[project_fpl_ingestion_discarded_fields]]).

> compare them on fixtures, minutes security, form and underlying numbers together

There is no minutes data at all. The only per-player signals are FPL `form`, season-cumulative Understat `xg`/`xa`, `status` and `news`. "Minutes security" is unanswerable, and a rotation risk is invisible until it shows up as bad form weeks later.

`DecisionKind` includes `"chip"` and the briefing describes all four chips, but nothing knows which are still available. `/entry/{id}/history/` returns the `chips` array.

The pattern here is the worst kind of prompt instruction: one the model cannot satisfy and will therefore either ignore or answer by inference. Ticket 22 turns the resulting rules into enforcement, and cannot be built until this data exists.

## Scope

- **From the picks endpoint (no new call):** bank, squad value, XI vs bench with bench order, captain and vice-captain, active chip, transfers made this gameweek.
- **From `/entry/{id}/history/` (one new call):** chips used and when, per-gameweek points and rank. Rank matters beyond bookkeeping — chasing or protecting a rank is exactly the standing context that should change risk appetite, and `preferences.md` already refers to it.
- **Selling price is not current price.** FPL sells a player at purchase price plus half the rise, rounded down. Affordability computed from `now_cost` is simply wrong for any player who has risen. ~~The picks endpoint gives `selling_price` and `purchase_price` per pick — store both and use `selling_price` for affordability.~~ **Wrong, verified live 2026-08-28:** the public picks endpoint returns only `element, position, multiplier, is_captain, is_vice_captain, element_type`. Per-pick prices are on `/my-team/{id}/`, which answers **403 without an authenticated session**.
- **Minutes, in two tiers.** Season-total `minutes` and `starts` are already in `bootstrap.elements` — the payload `run_ingestion` fetches every cycle — so the cheap tier costs nothing and should land unconditionally for the whole pool. Only *per-fixture* minutes history needs `element-summary/{element_id}/`, which is one call per player: do **not** fan that out across all ~700 pool players. Scope it to the squad plus any player the user asks about, cache it, and record the caching approach in the comments. An unbounded fan-out here would turn ingestion into a multi-minute job.

## Notes

- `get_my_team` and `get_injury_news` both read `players` and both need the new fields; keep one source of truth rather than two divergent squad queries.
- Extend the `Team`/`Player` models rather than adding a parallel state object — the reasoning layer already treats `get_my_team` as the state-of-the-world call.
- Follow ticket 10's cascade lesson ([[project_teams_cascade_bug]]): anything new keyed to `teams(id)` must survive `run_ingestion`, which is why the teams row is upserted rather than deleted. Check any new table against that before committing.
- The `players` table is still wholesale `DELETE`d and reinserted each run. Per-pick purchase prices must not be lost by that refresh — if they can only come from the picks endpoint, that is fine, but verify it rather than assuming.

## Acceptance criteria

- [x] Bank, squad value, and transfers made this gameweek are ingested and reach the reasoning layer
- [x] The XI, the bench with its order, the captain and the vice-captain are all ingested and distinguishable in `get_my_team`
- [~] Each squad player's `selling_price` and `purchase_price` are stored, and affordability is computed from `selling_price`, never from current price — **not possible from the public API**; see comments. Aggregate selling value is ingested and per-player prices are reported as an upper bound with an explicit flag.
- [x] Chips used and chips remaining are ingested from `/entry/{id}/history/` and available to the reasoning layer
- [x] Per-gameweek points and overall rank history are ingested
- [x] Season-total minutes and starts are ingested for the whole pool from `bootstrap.elements`, with no additional API calls
- [x] Per-fixture minutes history is available for squad players and for a player the user asks about, without fetching `element-summary` for the whole pool
- [x] A rotation risk (a player with poor minutes despite no injury flag) is visible to the reasoning layer, which currently sees only `status`
- [x] Ingestion time does not regress materially; the minutes-fetch strategy and its cache are documented in the ticket comments
- [x] Asking "can I afford X?" gets an answer grounded in real bank and selling prices rather than a statement that the budget is unknown
- [x] Asking "which chips do I have left?" is answered from data
- [x] No new table or column is wiped by the next `run_ingestion` cycle, verified by running ingestion twice


## Comments

### Built 2026-08-28

Every field the ticket said was being fetched and discarded now lands, and the
model demonstrably uses it — a live "can I afford Semenyo for Foden?" came back
with real arithmetic instead of the shrug that motivated the ticket:

> Semenyo is 8.5m, Foden is listed at 7.0m and you have 0.5m in the bank — 7.5m
> of budget against an 8.5m price, so the move is roughly 1.0m short even if
> Foden sold at his full listed price.

It then propagated the selling-price uncertainty in the correct direction
unprompted ("listed prices are an upper bound only, which makes the gap
slightly worse, not better") and returned `decision: roll`.

### The one AC that could not be met, and why

**The ticket's Scope section is wrong about selling prices.** It says the picks
endpoint carries `selling_price` and `purchase_price`. It does not — verified
live against the real entry:

```
picks[0] = {element, position, multiplier, is_captain, is_vice_captain, element_type}
GET /api/my-team/{id}/ -> 403 {"detail":"Authentication credentials were not provided."}
```

Those fields exist only behind an authenticated FPL session. Purchase price is
not derivable from public data, so per-player selling price cannot be computed,
only bounded.

**What was built instead.** `entry_history` does give `bank` and `value`, and
`value` is *team* value — the 15 selling prices **plus** the bank, not the squad
alone. (GW1 makes this unmissable: every squad in the game reads value=100.0
with 0.5 banked and a 99.5m squad. An earlier cut of this work stored `value`
in a column named `squad_value` and got affordability wrong by exactly the
banked amount; the column is now `team_value` and the squad figure is derived.)

From that, `squad_state.sellable_value` reports:

- `squad_selling_value` = team_value − bank, the real total the 15 raise.
- `selling_prices_exact` — true when that total equals the sum of current
  listed prices, which means nobody has changed price since purchase and
  affordability is genuinely exact. Early season it usually is.
- a `note` the tool description tells the model to pass on, naming the gap when
  there is one and instructing it to treat listed price as an **upper bound**.

Measured on the real squad today: 0.5m banked, 99.5m selling against 99.7m
listed — a 0.2m gap, correctly flagged rather than silently ignored.

Getting to exact per-player selling prices needs authenticated FPL access,
which is a decision for the user, not something to bodge.

### Minutes: the two-tier strategy and its cache

**Cheap tier** — `minutes`, `starts`, `starts_per_90` for all 616 pool players,
straight out of the bootstrap payload ingestion already fetches. Zero extra
calls, as the ticket required.

**Expensive tier** — `element-summary/{id}/` is one HTTP call per player, so it
is never fanned out. Two entry points only: the 15 squad players on ingest, and
one player on demand when the user names someone (`get_player` with a client).

**Cache rule.** Refetch only when a player's cached history does not reach the
latest finished gameweek. Keyed on `MAX(gameweek)` per player rather than "is
there a row for GW N", because a player who did not feature has no row for that
week at all — checking for the row would refetch every benched player every
cycle, forever. Rows live in `player_gameweek_history` with **no FK** to
`fpl_players`, which `run_ingestion` deletes wholesale; a FK would cascade the
cache away every run and re-incur every call.

**Measured cost.** First run 1m20s (15 element-summary calls); second run 1m04s
with **0** calls, against ~1m before this ticket. The steady state is unchanged.

### Rotation risk

`rotation_risk` is true for an **available** player averaging under 60 minutes
a gameweek (FPL's own full-appearance threshold), needing at least 90 total
minutes so one blank in GW1 cannot flag a whole squad. Deliberately not raised
for injured players — `status` already explains those, and double-flagging
would bury the case that is actually surprising: the fit player quietly being
rotated.

### Also worth knowing

`get_my_team`'s squad is ordered by `squad_position`, so the model reads a
lineup rather than a set — and its description now tells the model that
`squad_gameweek` **trails** `current_gameweek` before a deadline, because FPL
only publishes picks after a lock. Without that it would present last week's XI
as this week's.

`opponent` is left NULL on the on-demand minutes path: FPL returns an opponent
*team id*, and no id→club-name map exists outside ingestion. NULL rather than
wrong; a `clubs` table would fix it and ticket 21 will want one anyway.

### Tests

`tests/test_squad_state.py` (16) plus 6 ingestion-level tests, including the
bank-subtraction regression, the cache making zero calls on a re-run, and AC 12
verified by running ingestion twice.

Two pre-existing tests were found to be **order-dependent**: `run_ingestion`
does an unscoped `DELETE FROM fixtures`, so anything running after the
ingestion tests sees an empty fixtures table. Both now assert against what
`planning_gameweek` actually resolves to rather than a literal. This is the
shared-test-database problem again, in its third distinct guise this week.
