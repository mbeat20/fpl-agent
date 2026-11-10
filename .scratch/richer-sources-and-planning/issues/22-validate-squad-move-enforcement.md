# 22 — `validate_squad_move`: legality enforcement that refuses

**What to build:** A tool that decides whether a proposed move is legal and affordable, and refuses it when it is not. Turns the squad rules in `fpl_rules.py` from prose the model can be argued past into a check it cannot route around.

**Blocked by:** 20 — Complete the FPL state: bank, XI/bench, captain, chips, minutes

**Status:** ready-for-human

> Note on history: a hard validator was offered and **declined** on 2026-08-27, which is why `fpl_rules.py`'s prose briefing exists as the softer guard. The user approved building it later the same day. Anything in project notes saying this was declined is superseded by this ticket.

## Why

`fpl_rules.py` says so itself, in its own module docstring:

> This is guidance, not enforcement — a prompt can be argued past. Constraints that must never be violated belong in a tool that refuses, not here.

That tool was never built. Today nothing in the codebase checks a single squad rule:

- `recommend_transfers(player_out, player_in, is_hit)` calls `get_player` twice and computes `gain_signal` from xG + xA + form. **It never compares the two players' positions.** A midfielder-out-for-a-forward move — which FPL will not accept — is priced, ranked, given creator opinions on both sides, and returned as a legitimate comparison.
- Nothing checks the 3-players-per-club limit.
- Nothing checks affordability, because until ticket 20 there was no bank and no selling price to check against.
- Nothing checks that the incoming player isn't already in the squad.

The briefing tells the model to catch all of this by reading `position` fields itself. It was written *because* illegal advice had already reached the user as a confident recommendation. A prompt is the wrong place for an invariant.

## Approach

- `validate_squad_move` returns a verdict plus the specific violations, not a boolean. "Illegal: Salah is a MID, Watkins is a FWD" is actionable; `false` is not.
- Wire it as a genuine refusal, not advice: `recommend_transfers` must validate **before** it computes anything, and return the violation instead of a comparison. A `gain_signal` on an impossible move is misinformation regardless of what prose accompanies it.
- The same check must gate the finalisation path. `submit_recommendation` with `decision: "transfer"` should not be able to return an illegal move — the ADR-0004 principle that a pick is built from an authoritative lookup rather than model text applies equally to a pick's legality.
- Cover the rules that are actually checkable from stored state: position match, 3-per-club, already-owned, affordability from `selling_price` + bank, and squad composition (2/5/5/3) after the move. Formation legality of the resulting XI is a separate concern — either cover it or say explicitly that it isn't covered, rather than leaving it ambiguous.
- Keep ticket 11's `PUSHBACK:` behaviour intact and distinct: pushback means "legal but I think it's wrong", refusal means "this cannot be done". Conflating them would lose a real distinction the user relies on.
- Free-transfer accounting (1 per week, rolling, capped at 5) and hit arithmetic belong here too, since ticket 20 now ingests `event_transfers`. A "free" transfer the user has already spent is not free.

## Acceptance criteria

- [x] A position-mismatched transfer is refused with a message naming both positions, and no `gain_signal` is computed or returned for it
- [x] A move breaching the 3-per-club limit is refused, naming the club and the players already held
- [x] A move the user cannot afford is refused, computed from `selling_price` + bank, and reports the shortfall
- [x] A move bringing in a player already in the squad is refused
- [x] A move leaving the squad outside 2 GK / 5 DEF / 5 MID / 3 FWD is refused
- [x] `recommend_transfers` validates before comparing, and an illegal move never returns a comparison
- [x] A `submit_recommendation` of `decision: "transfer"` cannot finalise an illegal move
- [x] Ticket 11's `PUSHBACK:` path still works and is clearly distinct from a refusal in the output
- [x] Whether the transfer costs a hit is derived from ingested `event_transfers` and rolled free transfers rather than asked of the user or assumed
- [x] Formation legality of the resulting XI is either enforced or documented as explicitly out of scope
- [x] An integration test confirms the real reasoning layer, asked for an illegal move, reports it as impossible rather than proposing it

## Comments

**Built 2026-08-30.** Nine of eleven ACs done. The two `[~]` are the
integration ACs — both tests are written, neither has been executed (they cost
real Claude calls).

### Shape

- `squad_rules.py` — the rules as pure functions over dicts. No database, no
  models, so they are testable and readable on their own.
- `tools/validate_squad_move.py` — reads the real squad, finances and transfer
  history and produces a verdict. **The single place both enforcement paths go
  through**, so a move refused in one cannot be approved by the other.
- `recommend_transfers` validates first and returns the verdict instead of a
  comparison when the move is illegal.
- `ClaudeReasoning` gained an optional `validate` hook, gating the
  finalisation path.

### A verdict, not a boolean

"Illegal: Salah is a MID, Watkins is a FWD" is actionable; `false` is not, and
a model handed `false` will guess at why. Every violation carries a stable
`rule` slug plus prose that names the specifics — the club and the three
players already held, the exact shortfall in millions, both positions by name.

All violations are collected rather than short-circuiting, so one round trip
surfaces everything wrong with a move.

**One deliberate exception.** The composition rule is skipped when a position
mismatch, a missing outgoing player, or an already-owned incoming player has
already fired. On a legal 15 a like-for-like swap cannot change the
composition at all, so the rule only ever fires as a *consequence* of those —
"you would hold 4 DEF and 4 FWD" is "Shaw is a DEF and Haaland is a FWD"
restated, and printing both buries the reason that matters. It still earns its
place for the one case nothing else covers: a squad ingested already out of
shape. Measured on the real squad before the fix, `Shaw -> Haaland` returned
five violations, two of which were the same fact.

### Refusal is not pushback

Ticket 11's `PUSHBACK:` means "legal, but I think you are wrong" and invites an
argument. A violation means "this cannot be done" and ends one. Both the tool
description and a new clause in `fpl_rules.py` say so explicitly, and a legal
verdict's own `summary` says "this is not an argument that the move is a good
one" so a legality pass can never read as an endorsement.

### The finalisation gate, and what it can honestly check

`submit_recommendation` names only the player coming IN, so a full validation
is impossible there. `validate_incoming` is a **possibility** check instead: is
there any legal version of this move at all?

- already owned;
- the squad holds nobody of his position to sell;
- every player from his club is a different position, so no like-for-like sale
  can free the slot;
- unaffordable even after selling the most expensive player of that position
  plus the bank — the best case, so failing it means no pairing works.

Deliberately permissive about a specific bad pairing: the model cannot tell the
gate which pairing it meant, and a false refusal would teach it to route around
the check. `recommend_transfers` is where a named pairing is fully validated.

The hook lives on the `Reasoning` protocol rather than inside `ClaudeReasoning`
because it is a guarantee about the OUTPUT, not a provider detail — ADR-0004
already says the pick is built from an authoritative lookup rather than model
text, and "and it must be legal" is that principle one step further. A refused
submit goes back through the same `is_error` channel a malformed one already
used, so the model gets the violation and another turn. Applied to
`TransferPlan` steps too: a plan's week-3 step being impossible is not less
wrong for being three weeks away.

### Free transfers: a real bug the tests caught

The first implementation derived `is_hit` from the accrual alone, and **it
could never fire**. FPL grants one free transfer every gameweek regardless of
how many were spent before, so the accrued count is never below 1 and
`available < 1` was dead code.

The count is now two steps: accrue up to the planning gameweek, then subtract
the transfers recorded *in* that gameweek. That second step is what can reach
zero, and it is exact once a deadline has passed. Before the deadline FPL
publishes no transfer count for the gameweek being planned, so the number is
an upper bound and the verdict's `note` says exactly that — different prose in
each case, so "2 free transfers" is never presented as certain when it is not.

A wildcard or free-hit gameweek neither consumes the free transfer nor costs a
hit, and FPL retains saved transfers across both, so those gameweeks are
skipped in the simulation.

`recommend_transfers` now lets the derived count override a caller's
`is_hit=False`. "Assume it's a free transfer" is the commonest thing a user
says and the commonest thing that is wrong, and this is no longer guessable.

### Formation legality: explicitly out of scope, with a reason

A transfer is like-for-like by position and the incoming player inherits the
outgoing player's squad slot, so a legal transfer cannot change which
formations the XI can field. The check would always pass, and shipping it
would imply a guarantee about bench ordering this module does not make. XI
selection and bench order are a separate decision and belong with **ticket
23's deadline checklist**.

### Tests that had to change, and why that is the point

`test_recommend_transfers.py` was built entirely on **"Out Player" (MID) →
"In Player" (FWD), with neither player in any squad** — the exact move this
ticket exists to refuse. It had been asserting `gain_signal`, hit arithmetic,
injury flags and creator opinions on an impossible transfer since ticket 11.
The fixture now seeds a real team row with a bank and a squad containing Out
Player, and In Player is a MID. Its team id moved from 90218 to 90223:
`test_stance_recency` already owns 90218, and two files creating and deleting
the same team row is the order coupling that has bitten this suite twice.

### Verification

- `test_validate_squad_move.py`: 28 tests, all passing. Every rule, both
  directions (the club limit refuses a fourth *and* allows a swap that frees
  the slot); the budget as selling price plus bank; violations collected not
  short-circuited; composition not double-reported; the free-transfer
  simulation including the wildcard case, the cap, and the accrual floor; and
  the four `validate_incoming` refusals plus the permissive case.
- `test_claude_reasoning.py`: 3 new tests on the gate mechanic with the
  Anthropic client mocked — an illegal submit is bounced with the reason and
  the model's revised answer is what returns; a legal one passes untouched in
  a single turn; a plan step is gated the same way.
- Smoke-tested against the **real 15-player squad**: `Foden -> Semenyo`
  correctly 1.0m short, `Shaw -> Haaland` refused on four counts,
  `Wirtz -> Doku` refused on the 3-MCI limit naming Foden/Gvardiol/Haaland,
  and freeing the club slot by selling from that club allowed the move.

### Open

- Both integration tests are written and unrun:
  `test_claude_reports_an_illegal_move_as_impossible_rather_than_proposing_it`
  (asserts on the prose, not just the pick — the failure being guarded is
  answering a question about an impossible move as though it were a close
  call) and the pre-existing PUSHBACK test, which must still pass unchanged.
  `uv run python -m pytest -m integration -k "illegal or pushback"`.
- **Exact selling prices still need an authenticated FPL session.** Where FPL's
  team value shows a player sells below his listed price, affordability is
  reported as an upper bound rather than refused — refusing on a number known
  to be an over-estimate would block legal moves. This is the open question
  ticket 20 raised, and it is now the last real gap in the validator.
- **Ticket 25 extends this over time**: the same rules walked across a
  sequence, with cumulative budget and free-transfer accounting. `squad_rules`
  is deliberately pure functions over dicts so a plan validator can reuse them
  step by step without a database round trip per step.

### The integration file had rotted, and one of its fixtures was this ticket's own bug

`test_recommendations_integration.py` is excluded from the default run, so
nothing had exercised it since ticket 17. Running it surfaced **eight**
failures, none of them caused by this session's code and all of them worth
recording because they are the same two failure classes over and over.

**Signature rot (5 tests).** Ticket 18 added `team_id` to
`make_recommend_transfers_tool` and `make_search_creator_opinions_tool` and
updated every caller *except* this file. Straight `TypeError` on collection.

**Fixtures that assumed an empty database (2 tests).** Both were green only
while `fpl_players` held nothing but their own rows:

- `non_squad_player` seeded a fake "Ollie Watkins" at id 921 while the real
  pool holds the real one at **id 55**, so `find_player`'s
  `... LIMIT 1` with no `ORDER BY` could return either. Renamed to a player
  who cannot exist.
- `pool_with_a_clear_price_riser` gave its riser 15,000 net transfers,
  "distinct enough that a real Claude call should flag it". Against the live
  pool the leading riser measured **429,408** and the fixture did not reach
  the top ten. Its figure is now larger than any real player can reach.

  Its assertion also had to change. `pick.player_id == 981` was pinning which
  of ten genuine risers a real model prefers, which is a coin flip that says
  nothing about the criterion. It now asserts the pick is one of the ids
  `predict_price_changes` actually returned and is **not** a squad player,
  which is what issue 08's AC ("not just pick an arbitrary squad player")
  actually says.

The identical collision class was fixed in `test_get_player.py` and
`test_compare_players.py`, which seeded fake "Saka" and "Haaland" rows and
were among the **6 failures in the full default suite** on this session's
first complete run. They had been green in earlier sessions only because a
suite run ends with `test_ingestion` wiping `fpl_players`, so the *next* run
started from an empty pool — running `fpl-ingest` afterwards, as the standing
rule says to, is what exposed them.

**One fixture was this ticket's own doing, and that is the point.**
`transfer_candidates` paired a MID going out with a FWD coming in, and the
outgoing player was in no squad at all. Four integration tests had been
asserting `gain_signal`, hit arithmetic and creator opinions on a move FPL
will not accept — the exact failure this ticket exists to stop, sitting
unnoticed in the tests since ticket 11. Both are midfielders now and Weak
Player is in the seeded squad.

That broke the PUSHBACK test in an instructive way: it runs the swap in the
**other** direction, so it needs the good player owned and a weak target
unowned, and a player cannot be both. It got its own fixture
(`squad_holding_the_strong_player`, with `fpl_element_id = 972` so
`get_my_team` reports him under the pool's id). The move it proposes is now
entirely legal, so the only thing that can stop it is judgement — which makes
it a genuinely sharp test of refusal-versus-pushback rather than an accident.

`test_recommend_transfers_is_the_only_way_to_reach_a_player_it_surfaces` also
did `recommendation.alternative.player_id` with no None check, and `alternative`
is legitimately absent when there is no genuine second option (claude.py drops
it rather than substituting a filler). It raised `AttributeError` instead of
failing usefully.

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
