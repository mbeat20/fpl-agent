# 25 — Persisted roadmap, plan validator, and weekly check-in

**What to build:** The multi-week plan stops being a one-off answer and becomes a thing that exists: stored, checked for legality week by week, revisited when the world changes, and reviewable afterwards. This is the "give me a roadmap for the coming weeks" feature.

**Blocked by:** 21 — Fixture-run tool; 22 — `validate_squad_move`; 24 — Durable outcome recording

**Status:** ready-for-human

## Why

Ticket 12 built `TransferPlan` and it works — a long-horizon question returns multi-gameweek steps. But the plan evaporates the moment the response renders:

- **It is never persisted.** Only the nearest step reaches `recommendation_log` (`api/routes.py`, `_log_result`), and deliberately so, because `record_pending_outcomes` could not resolve future-dated rows. Ticket 24 removes that constraint.
- **It cannot be revisited.** CONTEXT.md defines Horizon as "resolved live from fixtures/data each time. Never persisted." That is right for the *Horizon of a question* and wrong for a *roadmap the user is following*. These are different things and the domain model should say so — this ticket needs a CONTEXT.md entry and probably an ADR, not just a table.
- **Its arithmetic is unchecked.** Nothing verifies that step 3 is affordable given steps 1 and 2, that the free transfers exist, or that the squad is still legal four weeks in. The model does this in prose, across five steps, with no checker.

A roadmap the user cannot revisit, and cannot be shown to have drifted from, is a conversation — not a plan.

## Scope

**Persistence.** One live roadmap per team, versioned rather than overwritten. Versioning is the point: the value is in seeing that last week's roadmap said something different and knowing why. Overwriting destroys exactly the signal `preferences.md` asks for ("if the pick changed, be able to say why — genuine reassessment vs. bandwagon influence").

**Validation, deterministic.** Walk the proposed sequence step by step and return per-step violations: budget after prior steps, free transfers available versus spent, hits taken, 3-per-club, squad composition, and any step whose player has a blank in that gameweek. The reasoning layer should iterate against this checker rather than doing multi-week arithmetic in prose — same principle as ticket 22, extended over time. **A plan that fails validation must not be returned as a plan.**

**Weekly check-in.** Given the live roadmap and the current state, report what changed: a step invalidated by an injury, a fixture that has moved or become a blank (ticket 21 notes fixtures are wholesale-refreshed each cycle, so a stored plan rots silently), a price move that broke affordability. Then let the user accept a revision or keep the plan.

**Review.** Once ticket 24 makes outcomes durable, a completed roadmap can be scored against what actually happened — including steps the user chose not to take. Judge the process, not just the result: `preferences.md` is explicit that a good decision can produce a bad outcome, so a review that only reports points would actively contradict the user's own stated principle.

## Notes

- Anchor steps to absolute gameweeks, as `PlanStep.gameweek` already does. Do not reintroduce relative offsets.
- Steps may legitimately share a gameweek (a free transfer plus a hit) — ticket 12 hit exactly this and loosened a test for it. Do not assume one step per gameweek, and do not assume steps arrive in gameweek order.
- Persisting a plan makes it stale by default. Decide when a roadmap is considered dead rather than letting the user act on a month-old plan that still looks authoritative.
- Keep the single-gameweek path untouched. Ticket 12's whole design was that a long horizon is a separate response type, not a field on `Recommendation`.

## Acceptance criteria

- [x] A multi-week roadmap is persisted, with one live roadmap per team and prior versions retained rather than overwritten
- [x] A deterministic validator returns per-step violations for budget, free-transfer count, hits, 3-per-club, squad composition, and blank gameweeks
- [x] A plan failing validation is never returned to the user as a plan; the reasoning layer revises against the validator's feedback
- [x] Cumulative arithmetic is checked across steps, not per step in isolation — step 3's affordability accounts for steps 1 and 2
- [x] The user can ask what changed since the roadmap was made and get the specific invalidated steps and why
- [x] A fixture that has become a blank or a double since the roadmap was stored is caught by the check-in
- [x] Accepting a revision creates a new version and records what changed; the previous version stays readable
- [ ] A completed roadmap can be reviewed against actual outcomes, including steps not taken, and reports on process rather than points alone
- [x] Steps sharing a gameweek, and steps arriving out of gameweek order, are both handled
- [x] CONTEXT.md distinguishes a persisted Roadmap from the never-persisted Horizon, and the decision is recorded as an ADR
- [x] Single-gameweek Recommendations are unaffected, verified by the existing ticket 12 tests still passing

## Comments

**2026-08-30 — built, except the review (blocked by 24).**

Ten of the eleven acceptance criteria are done. The review AC is not, and cannot be: it needs durable outcome recording, which is ticket 24 and is unbuilt. `recommendation_log` currently holds **zero rows** and `record_pending_outcomes` still resolves only the immediately-prior gameweek, so a completed roadmap can be checked against the *present* but not scored against the *past*. Left unticked rather than fudged.

New: `plan_rules.py` (pure), `roadmap.py` (DB), `roadmaps` table, three routes, `RoadmapPanel`, ADR-0016, a CONTEXT.md entry, and 30 tests.

### The one change that made everything else possible

`PlanStep` carried only `pick` — the player coming **in**. That single omission is why a plan's arithmetic had never been checked and could not be: without knowing who leaves, the bank after step 2 is unknowable, and therefore so is the squad, step 3's affordability, and its 3-per-club count. The model was doing all of it in prose, across five steps, with nothing verifying any of it.

So `PlanStep.player_out` was added, and `submit_plan` gained `out_player_id` drawn from the **same recorded-id enum as the pick** — ADR-0004's rule applied to the other side of the transfer, so a plan cannot sell a player the model invented. A transfer step that omits it is refused outright rather than accepted with a caveat, because a plan that cannot be checked is exactly the plan that should not be returned.

Worth noting what the enum cannot do: a player sold in GW5 is a perfectly valid id and an *invalid sale* in GW7. Squad membership is checked by the validator against the squad **at that step**, never by the schema. `test_selling_the_same_player_twice_is_caught` is that case.

### Cumulative, not per-step

`walk_plan` carries squad, bank and free transfers forward. Concretely, from the tests:

- Three steps each selling a 7.0 to buy dearer leaves the bank at 0.0, then −1.0 — caught at step 2, priced against what steps 0 and 1 left behind.
- Selling a ZKC player in GW50 **frees the club slot** for a ZKC signing in GW51. Checked against today's squad this would be a false refusal, and a false refusal teaches the model to route around the validator.
- Free transfers accrue across gaps (one per gameweek, capped at 5) and the plan's own transfers spend them, so four transfers in one gameweek off one free transfer correctly totals **12 points of hits** — the kind of number that is obvious in aggregate and invisible step by step.

Ordering was handled explicitly rather than assumed, per the ticket's note: steps are walked in **gameweek** order with ties broken by **submitted** order, and violations report against the submitted index because that is the only handle the model has on the step it wrote. Steps sharing a gameweek work (the stable tie-break decides which is the hit); steps arriving out of order work.

Every step is reported, not just the first broken one. Stopping early would surface one problem per round trip and hide everything downstream — twenty questions with a validator instead of a fixed plan.

### Legality versus soundness — ticket 22's distinction, kept

A step aimed at a club with no fixture that gameweek is **legal** and guarantees zero. Collapsing that into "illegal" would cost the distinction the user relies on (a violation ends an argument; pushback invites one); ignoring it would leave ticket 21's blank detection unused at exactly the horizon where blanks matter most.

So `PlanValidation.legal` covers only FPL-enforced rules, a blank is still a `violation`, and the finalisation gate refuses on **any** violation. `test_a_blank_is_a_violation_but_not_an_illegality` pins it.

### Versioning, and why every plan is stored

Every save inserts a new version, marks the previous one superseded with a reason written onto the **old** row, and leaves it fully readable. One live version per team is enforced by a **partial unique index**, not by convention — two live roadmaps is not a state the app should be able to reach by accident.

Every multi-week answer is stored, not only ones the user explicitly saves. Versioning is what makes that safe: a what-if creates version 4, it does not destroy version 3. An explicit save button would mean the common case — ask for a plan, then act on it — silently stored nothing.

The ticket asked when a roadmap is dead. A roadmap whose every step's gameweek has passed is `completed`, not `live`. That needs no arbitrary age cutoff and stops a month-old plan looking authoritative on its own.

### The check-in needed a snapshot, not today's fixtures

This was the subtle one. Ticket 21 notes `fixtures` is wholesale replaced every `run_ingestion`, so a stored plan **rots silently** — a gameweek can become a blank with nothing recording that it ever was not. Reading today's fixtures can report what is true; only a snapshot taken at save time can report what *changed*. "MCI have no fixture in GW18" is a fact the user may already know. "MCI **had** a fixture in GW18 when you made this plan" is the reason to reopen it.

So `roadmaps.fixture_snapshot` stores `{club: {gameweek: count}}` for what the plan touches, and the check-in diffs it — `became_blank`, `became_double`, `fixture_added`, `fixture_removed`. Both blank and double are tested by mutating `fixtures` after the save.

Alongside that the check-in catches injuries (against the **pool**, not just the squad — the player a step transfers *in* is exactly the one whose injury invalidates it) and re-runs the validator against today's prices, which is how a price rise that broke affordability surfaces.

Two deliberate scoping decisions:

- **Only remaining steps are revalidated**, from today's real squad. Re-checking elapsed steps would report problems with decisions already made and bury the ones still actionable.
- **Elapsed is not broken.** A plan that has simply progressed is separated out and `still_valid` stays true; the UI dims those steps rather than flagging them.

Violations from the forward slice are re-indexed back onto the original plan, since the user and the model both refer to steps by where they sit in the roadmap they were shown.

Like ticket 23's checklist, the check-in **reports and stops** (ADR-0015). It does not write a revision — that is a reasoning call the user chooses to make, not something the app does to their plan behind their back.

### Tests

- `tests/test_plan_rules.py` — 19, pure, no database, 2 seconds. The arithmetic is the deliverable and should be assertable without seeding a squad to get at it.
- `tests/test_roadmap.py` — 19, against real Postgres on team 90224 / gameweeks 60-64.
- `tests/test_claude_reasoning.py` — 2 added, covering `out_player_id` reaching `PlanStep.player_out` and an unrecorded id being dropped rather than substituted.

One self-inflicted bug worth recording: the first `test_roadmap.py` squad had **five and six players from the same club**, so every step failed `three_per_club` for reasons having nothing to do with the step. A test fixture that is itself illegal produces failures that look like validator bugs. The squad is now five clubs of three, and the buyable pool sits in a sixth club holding no squad player.

### Not done, and why

- **Review against outcomes** — blocked on ticket 24, as above. When it lands, note that it must judge the **process**, not the points: `preferences.md` is explicit that a good decision can produce a bad outcome, so a review reporting points alone would actively contradict the user's own stated principle.
- **`roadmaps` is teams-cascaded**, like `recommendation_log`, so the test suite's `seeded_team` teardown wipes team 1's roadmap history. That is the same shared-database problem raised across several sessions and is not solvable inside this ticket.
- **No revise-in-place endpoint.** Accepting a revision is just asking for a new plan, which creates a new version and leaves the old one readable — the AC is met by the save path rather than by a separate accept flow.
