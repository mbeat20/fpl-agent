# A Roadmap is persisted and versioned; a Horizon still is not

CONTEXT.md has said since ticket 12 that a Horizon is "resolved live from fixtures/data each time. Never persisted." That is correct and stays correct — a Horizon is a property of a *question*. How far ahead "should I captain Salah this week?" looks is not a fact about the user, and storing it would turn one question's framing into a standing setting.

But ticket 12 also shipped `TransferPlan`, and applied the same rule to it by omission. The plan evaporated the moment the response rendered. Only its nearest step reached `recommendation_log`, and deliberately so, because `record_pending_outcomes` could not resolve future-dated rows. The result was that the app could produce a five-week plan and, one week later, had no idea it had ever said anything.

**A Roadmap is a different kind of thing from a Horizon, and needs the opposite treatment.** It is a plan the user is *following*, and the whole value of that is being able to say next week that they have drifted from it — or that the world has. That is impossible against something never written down.

## Versioned, never overwritten

`team_suggestions` (ticket 15) overwrites, and that is right for a cache: a stale suggestion has no use. `roadmaps` does the opposite, and the reason is `preferences.md`:

> if the pick changed, be able to say why — genuine reassessment vs. bandwagon influence

That question is unanswerable against a row replaced in place. Version 3 saying something different from version 2 *is* the evidence. So every save inserts a new version, marks the previous one superseded with a reason written onto the **old** row, and leaves it fully readable. At most one version is live per team, enforced by a partial unique index rather than by convention — two live roadmaps is not a state the app should be able to reach by accident.

A consequence worth stating: **every** multi-week answer is stored, not only ones the user explicitly saves. Versioning is what makes that safe. Asking a what-if creates version 4; it does not destroy version 3. An explicit save button would mean the common case — ask for a plan, then act on it — silently stored nothing.

## A plan step must name both sides of the transfer

`PlanStep` carried only `pick`, the player coming in. That single omission is why none of a plan's arithmetic could be checked: without knowing who leaves, the bank after step 2 is unknowable, and so is the squad, and so is step 3's affordability and its 3-per-club count.

`PlanStep.player_out` closes it, and `submit_plan` gained an `out_player_id` drawn from the same recorded-id enum as the pick — ADR-0004's rule applied to the other side of the transfer, so a plan cannot sell a player the model invented. A transfer step that omits it is **refused**, not accepted with a caveat: a plan that cannot be checked is precisely the plan that should not be returned.

Note what the enum cannot do. A player sold in GW5 is a perfectly valid id and an invalid sale in GW7. Membership is therefore checked by the validator against the squad *at that step*, not by the schema.

## The validator walks the sequence, and refuses on more than illegality

`plan_rules.walk_plan` carries squad, bank and free transfers forward across the steps, in gameweek order, with ties broken by submitted order — both halves matter, because ticket 12 established that steps arrive in no guaranteed order *and* that they legitimately share a gameweek (a free transfer plus a hit).

It reports every step's problems rather than stopping at the first. Stopping would surface one problem per round trip and hide everything downstream, which is how a model ends up playing twenty questions with a validator instead of fixing its plan.

Two kinds of finding are kept apart:

- **Illegal.** FPL will not accept it. `legal` is false, and per ticket 22 that ends an argument rather than inviting one.
- **Legal and self-defeating.** A step aimed at a club with no fixture that gameweek. FPL accepts it happily; the player scores nothing. `legal` stays true, the violation is still reported, and the finalisation gate refuses the plan anyway — stopping plans being aimed at blanks was the entire point of ticket 21.

Collapsing those two would have cost the distinction the user relies on. Reporting only the first would have let ticket 21's work go unused at exactly the horizon where blanks matter most.

## The check-in needs a snapshot, not just today's fixtures

Ticket 21 noted that `fixtures` is wholesale replaced on every `run_ingestion`. A stored plan therefore rots **silently**: a gameweek can become a blank with nothing recording that it ever was not.

So a fixture snapshot — `{club: {gameweek: count}}` for what the plan touches — is stored alongside the plan. Without it the check-in can report what is true today but not what *changed*, and only the second is news. "MCI have no fixture in GW18" is a fact the user may already know; "MCI *had* a fixture in GW18 when you made this plan and now do not" is the reason to reopen it.

The check-in reports and stops, exactly like ticket 23's checklist (ADR-0015). It names the steps that no longer hold and why, and does not write a replacement — revising is a reasoning call the user chooses to make, not something the app does to their plan behind their back. It revalidates only the **remaining** steps, from today's real squad: re-checking elapsed steps would report problems with decisions that are already history and bury the ones still actionable.

## When a roadmap is dead

A stored plan that keeps looking authoritative for a month is worse than no plan. The honest definition needs no arbitrary age cutoff: a roadmap whose every step's gameweek has passed is `completed`, not `live`. Nothing in it is actionable, and the status says so.

## What this does not yet do

Reviewing a completed roadmap against what actually happened — including the steps the user chose not to take — needs durable outcome recording, which is ticket 24 and is not built. `recommendation_log` currently holds nothing, and `record_pending_outcomes` still resolves only the immediately-prior gameweek. Until that lands, a roadmap can be checked against the *present* but not scored against the *past*.

When it does land, the review must judge the process rather than the points. `preferences.md` is explicit that a good decision can produce a bad outcome, so a review reporting only points would actively contradict the user's own stated principle.
