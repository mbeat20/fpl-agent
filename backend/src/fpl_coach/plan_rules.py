"""Issue 25: the squad rules applied over TIME, not to one move.

`squad_rules.py` answers "is this transfer legal". That is enough for a single
gameweek and useless for a plan, because a plan's third step is legal or not
depending on what its first two did. Ticket 12 shipped multi-week plans and
nothing ever checked that arithmetic -- the model did it in prose, across five
steps, with no checker. The failure mode is not exotic: sell a 7.0 midfielder
in GW5 to fund an 8.5 in GW6, and whether GW7's move is affordable depends on
a bank balance nobody computed.

So this module walks the sequence, carrying state forward:

- **The squad changes.** Step 2's "3 players per club" is counted against the
  squad step 1 produced, not the one the user holds today. Otherwise selling a
  Liverpool player in GW5 never frees the slot the GW6 step needs.
- **The bank changes.** Each step's budget is the outgoing player's selling
  price plus whatever the previous steps left behind.
- **Free transfers accrue and are spent.** One per gameweek, capped at five,
  and the plan's own transfers consume them. Two transfers in a gameweek
  holding one free transfer is a -4, and a plan can quietly rack up three of
  those.

**Why a step must name the player going out.** Ticket 12's `PlanStep` carried
only `pick` -- the player coming in. That is precisely why none of the above
could be checked: without knowing who leaves, the budget after step 2 is
unknowable and so is the squad. `PlanStep.player_out` exists for this, and a
transfer step that omits it is refused rather than waved through, because a
plan that cannot be checked is exactly the plan that should not be returned.

**Legality versus soundness.** `legal` covers only rules FPL itself enforces
-- a violation there ends an argument (ticket 22). A blank gameweek is not one
of those: captaining a player with no fixture is perfectly legal and merely
guarantees zero. It is still reported as a violation, because the whole point
of ticket 21 was to stop plans being aimed at blanks, and the caller refuses a
plan carrying any violation at all. The two are kept apart in `legal` so the
distinction the user relies on survives.
"""

from dataclasses import dataclass

from fpl_coach.squad_rules import (
    HIT_COST,
    MAX_FREE_TRANSFERS,
    PRICE_TOLERANCE,
    Violation,
    affordability_violation,
    already_owned_violation,
    club_limit_violation,
    composition_violation,
    position_violation,
)

# Rules FPL itself enforces. A violation here means the sequence cannot be
# executed at all; anything else means it can be executed and should not be.
LEGALITY_RULES = frozenset(
    {
        "position_mismatch",
        "three_per_club",
        "already_owned",
        "not_in_squad",
        "squad_composition",
        "affordability",
        "past_gameweek",
        "missing_player_out",
        "missing_player_in",
    }
)


@dataclass(frozen=True)
class PlannedStep:
    """One step of a plan, resolved to real players.

    `index` is the step's position in the plan AS SUBMITTED. Steps are walked
    in gameweek order but reported by submitted index, because that is the
    only handle the model has on the step it wrote -- and ticket 12 already
    established that steps arrive in no guaranteed order.
    """

    index: int
    gameweek: int
    decision: str
    # {name, position, club, price} or None. A 'roll' step has neither.
    player_in: dict | None = None
    player_out: dict | None = None


@dataclass
class _State:
    squad: list[dict]
    bank: float | None


def _sorted_steps(steps: list[PlannedStep]) -> list[PlannedStep]:
    """Gameweek order, ties broken by submitted order.

    Both halves matter. Ticket 12 found that steps arrive in no guaranteed
    order, so walking them as submitted would price a GW8 move against a bank
    that a GW6 move had not yet touched. And steps legitimately SHARE a
    gameweek -- a free transfer plus a hit -- so the tie-break has to be
    stable rather than arbitrary, or which of the two counts as the hit would
    change between runs.
    """
    return sorted(steps, key=lambda step: (step.gameweek, step.index))


def _accrue(free: int, from_gameweek: int, to_gameweek: int) -> int:
    """Free transfers after the weeks between two gameweeks pass.

    One per gameweek, capped. Never decreases: FPL grants a free transfer
    every gameweek regardless of what was spent before.
    """
    if to_gameweek <= from_gameweek:
        return free
    return min(MAX_FREE_TRANSFERS, free + (to_gameweek - from_gameweek))


def _transfer_violations(step: PlannedStep, state: _State, exact: bool) -> list[Violation]:
    """Every squad rule, checked against the SIMULATED squad and bank."""
    if step.player_in is None:
        return [
            Violation(
                rule="missing_player_in",
                message=(
                    f"The GW{step.gameweek} step is a transfer but names no incoming "
                    "player. A transfer step needs both the player coming in and the "
                    "player going out."
                ),
            )
        ]
    if step.player_out is None:
        return [
            Violation(
                rule="missing_player_out",
                message=(
                    f"The GW{step.gameweek} step transfers in {step.player_in['name']} "
                    "but does not say who is sold. Name the outgoing squad player: "
                    "without it the budget for every later step is unknowable, so the "
                    "plan cannot be checked and will not be accepted."
                ),
            )
        ]

    violations: list[Violation] = []
    held = next(
        (p for p in state.squad if p["name"].casefold() == step.player_out["name"].casefold()),
        None,
    )
    if held is None:
        violations.append(
            Violation(
                rule="not_in_squad",
                message=(
                    f"The GW{step.gameweek} step sells {step.player_out['name']}, who is "
                    "not in the squad at that point in the plan. Either an earlier step "
                    "already sold them, or they were never owned."
                ),
            )
        )
        return violations

    owned = already_owned_violation(state.squad, step.player_in)
    if owned is not None:
        violations.append(owned)

    mismatch = position_violation(
        held["position"], step.player_in["position"], held["name"], step.player_in["name"]
    )
    if mismatch is not None:
        violations.append(mismatch)

    club_limit = club_limit_violation(state.squad, held, step.player_in)
    if club_limit is not None:
        violations.append(club_limit)

    # Same suppression as validate_squad_move: on a legal 15 a like-for-like
    # swap cannot change the shape, so composition only ever fires as a
    # consequence of a mismatch already reported. Stating both buries the
    # reason that matters.
    if not any(v.rule in {"position_mismatch", "already_owned"} for v in violations):
        composition = composition_violation(state.squad, held, step.player_in)
        if composition is not None:
            violations.append(composition)

    budget = None if state.bank is None else round(held["price"] + state.bank, 1)
    money = affordability_violation(
        budget, step.player_in["price"], step.player_in["name"], exact
    )
    if money is not None:
        violations.append(money)

    return violations


def _apply(step: PlannedStep, state: _State) -> None:
    """Carry the transfer into the running squad and bank.

    Applied even when the step had violations, deliberately. Stopping at the
    first bad step would report one problem per round trip and hide everything
    downstream of it; continuing reports the whole plan's worth of problems in
    one pass, which is what lets the model fix the plan rather than play
    twenty questions with the validator.
    """
    if step.player_in is None or step.player_out is None:
        return
    held = next(
        (p for p in state.squad if p["name"].casefold() == step.player_out["name"].casefold()),
        None,
    )
    if held is not None:
        state.squad = [p for p in state.squad if p is not held]
        if state.bank is not None:
            state.bank = round(state.bank + held["price"], 1)
    state.squad = state.squad + [step.player_in]
    if state.bank is not None:
        state.bank = round(state.bank - step.player_in["price"], 1)


def _blank_violation(step: PlannedStep, blanks: dict[str, set[int]]):
    """A step aimed at a player whose club has no fixture that gameweek.

    Legal, and worthless: the player scores nothing. Checked for any step that
    names a player, because captaining into a blank and transferring in for a
    blank are the same mistake wearing different hats.
    """
    player = step.player_in
    if player is None:
        return None
    if step.gameweek not in blanks.get(player["club"], set()):
        return None
    return Violation(
        rule="blank_gameweek",
        message=(
            f"{player['name']} plays for {player['club']}, who have no fixture in "
            f"GW{step.gameweek} -- a blank. This step is legal but the player scores "
            "nothing that gameweek. Move it, or pick a club that plays."
        ),
    )


def walk_plan(
    steps: list[PlannedStep],
    squad: list[dict],
    bank: float | None,
    free_at_start: int | None,
    start_gameweek: int | None,
    blanks: dict[str, set[int]] | None = None,
    selling_prices_exact: bool = False,
):
    """Walk a plan in gameweek order and report what breaks, and where.

    Returns (violations, step_costs, totals) as plain structures; the caller
    wraps them in the `PlanValidation` model. Kept model-free so the rules
    stay testable with dicts, the same way `squad_rules` is.
    """
    blanks = blanks or {}
    state = _State(squad=[dict(p) for p in squad], bank=bank)
    violations: list[tuple[PlannedStep, Violation]] = []
    costs: list[dict] = []

    ordered = _sorted_steps(steps)
    free = free_at_start
    # Accrual is measured from the planning gameweek. Falling back to the
    # plan's own first gameweek keeps the free-transfer arithmetic working
    # when no planning gameweek is known -- the count is then relative rather
    # than absolute, and the caller says so in `note`.
    current = start_gameweek if start_gameweek is not None else (
        ordered[0].gameweek if ordered else None
    )

    transfers = 0
    total_hits = 0

    for step in ordered:
        if start_gameweek is not None and step.gameweek < start_gameweek:
            violations.append(
                (
                    step,
                    Violation(
                        rule="past_gameweek",
                        message=(
                            f"The step for GW{step.gameweek} is in the past -- the "
                            f"gameweek being planned is GW{start_gameweek}. A plan "
                            "cannot start before the next deadline the user can act "
                            "on."
                        ),
                    ),
                )
            )

        if current is not None and free is not None:
            free = _accrue(free, current, step.gameweek)
        if current is not None:
            current = max(current, step.gameweek)

        bank_before = state.bank
        is_hit = False
        free_before = free

        if step.decision == "transfer":
            transfers += 1
            for violation in _transfer_violations(step, state, selling_prices_exact):
                violations.append((step, violation))
            if free is not None:
                is_hit = free < 1
                free = max(0, free - 1)
            if is_hit:
                total_hits += 1
            _apply(step, state)

        blank = _blank_violation(step, blanks)
        if blank is not None:
            violations.append((step, blank))

        costs.append(
            {
                "step_index": step.index,
                "gameweek": step.gameweek,
                "decision": step.decision,
                "player_in": step.player_in["name"] if step.player_in else None,
                "player_out": step.player_out["name"] if step.player_out else None,
                "free_transfers_before": free_before,
                "is_hit": is_hit,
                "hit_cost": HIT_COST if is_hit else 0,
                "bank_before": bank_before,
                "bank_after": state.bank,
            }
        )

    return violations, costs, {
        "transfers_planned": transfers,
        "total_hit_cost": total_hits * HIT_COST,
        "final_bank": state.bank,
    }


def summarise(legal: bool, violations: list, totals: dict) -> str:
    if not violations:
        cost = totals["total_hit_cost"]
        spend = (
            " It takes no hits."
            if cost == 0
            else f" It costs {cost} points in hits across {totals['transfers_planned']} transfers."
        )
        return (
            "This plan is legal and its arithmetic holds across every step."
            + spend
            + " That says the sequence can be executed -- it is not an argument that it"
            " is the right one."
        )
    if legal:
        return (
            f"REVISE -- this plan is legal but {len(violations)} step(s) are aimed at a "
            "gameweek where the player has no fixture. Fix them and resubmit."
        )
    return (
        f"REFUSED -- this plan cannot be executed as written. {len(violations)} problem(s) "
        "across its steps; each is reported against the step that causes it."
    )


def is_legal(violations: list) -> bool:
    """FPL-legality only. A plan can be legal and still carry violations --
    a step aimed at a blank is the case this distinction exists for."""
    return not any(v.rule in LEGALITY_RULES for _, v in violations)


def affordable_tolerance() -> float:
    """Exposed so tests assert against the same tolerance the rules use
    rather than restating 0.05 and drifting from it."""
    return PRICE_TOLERANCE


# --------------------------------------------------------------------------
# Issue 27: revising ONE step of a plan the user is already following.


class StepNotRevisable(ValueError):
    """The named step cannot be identified in the plan, so nothing is changed.

    Raised rather than resolved by guessing. Both cases it covers are ones
    where a plausible answer exists and is not safe to give: a gameweek with
    no step could be treated as an insertion, and a gameweek with two steps
    (a free transfer plus a hit, which `submit_plan` explicitly allows) could
    be answered by revising the first. Either would edit the user's stored
    plan into something they did not ask for. The message is handed back to
    the model, which can name the step properly on its next turn.
    """


def merge_step(plan, gameweek: int, replacement):
    """`plan` with the step at `gameweek` replaced by `replacement`.

    A new plan object -- the original is untouched, because a Roadmap is
    versioned and never overwritten (ADR-0016) and the stored version has to
    survive the merge intact to remain the thing the new version is compared
    against.

    Every other step is carried through **exactly as it was**, which is the
    whole point of the criterion this serves: regenerating a five-week plan to
    change week three loses the reasoning behind weeks one, two, four and
    five, and quietly changes steps the user never questioned.

    The merged plan still goes through `plan_problems` before it is stored.
    Revising one step is not a way around the validator -- a legal step can
    make a later step illegal by spending money it needed, and that is
    precisely the cumulative check `walk_plan` exists to do.
    """
    matches = [step for step in plan.steps if step.gameweek == gameweek]
    if not matches:
        planned = sorted({step.gameweek for step in plan.steps})
        raise StepNotRevisable(
            f"The plan has no step for GW{gameweek}. It covers {planned}. "
            "Name one of those, or call submit_plan to replace the whole plan."
        )
    if len(matches) > 1:
        raise StepNotRevisable(
            f"GW{gameweek} has {len(matches)} steps in this plan, so it is "
            "ambiguous which one to revise. Call submit_plan with the full "
            "revised plan instead."
        )

    target = matches[0]
    return plan.model_copy(
        update={
            "steps": [
                replacement if step is target else step
                for step in plan.steps
            ]
        }
    )
