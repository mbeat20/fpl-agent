"""Issue 22: the squad rules as a check that refuses, not prose that can be
argued past.

`fpl_rules.py` says so in its own module docstring:

    This is guidance, not enforcement -- a prompt can be argued past.
    Constraints that must never be violated belong in a tool that refuses,
    not here.

That tool did not exist. Nothing in the codebase checked a single squad rule:
`recommend_transfers` called `get_player` twice and computed a `gain_signal`
without ever comparing the two players' `position` fields, so a
midfielder-out-for-a-forward move -- which FPL simply will not accept -- was
priced, ranked, given creator opinions on both sides, and returned as a
legitimate comparison. That briefing was written *because* illegal advice had
already reached the user as a confident recommendation.

**A verdict, not a boolean.** "Illegal: Salah is a MID, Watkins is a FWD" is
actionable; `false` is not, and a model handed `false` will guess at why.

**Refusal is not pushback.** Ticket 11's `PUSHBACK:` means "legal, but I think
you are wrong". A violation here means "this cannot be done". Conflating them
would lose a distinction the user relies on: one invites an argument, the
other ends it.

**What is deliberately NOT enforced, and why.** Formation legality of the
resulting XI. A transfer in FPL is like-for-like by position and the incoming
player inherits the outgoing player's squad slot, so a legal transfer cannot
change which formations the XI can field -- the check would always pass and
would imply a guarantee about bench ordering that this module does not make.
Bench order and XI selection are a separate decision from a transfer, and
belong with ticket 23's deadline checklist.
"""

from dataclasses import dataclass

# FPL's squad shape. Not configurable and not inferred from the current
# squad -- inferring it would let an already-broken squad legitimise the
# move that broke it.
SQUAD_SHAPE = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
SQUAD_SIZE = sum(SQUAD_SHAPE.values())
MAX_PER_CLUB = 3
HIT_COST = 4
# Free transfers accrue one a gameweek and stop accumulating here.
MAX_FREE_TRANSFERS = 5
# FPL money is in tenths; float arithmetic on tenths needs a tolerance rather
# than an equality test. Matches squad_state._PRICE_TOLERANCE.
PRICE_TOLERANCE = 0.05

# Chips that make a gameweek's transfers free, so they neither consume the
# free transfer nor cost a hit. FPL retains saved free transfers across both.
_TRANSFER_FREEING_CHIPS = {"wildcard", "freehit"}


@dataclass(frozen=True)
class Violation:
    """One broken rule. `rule` is a stable slug so a caller can branch on it;
    `message` is what the user or the model actually reads, and names the
    specifics rather than restating the rule."""

    rule: str
    message: str


def position_violation(out_position: str, in_position: str, out_name: str, in_name: str):
    """The one the codebase got wrong. A transfer is strictly like-for-like:
    there is no such thing as trading a midfielder for a forward."""
    if out_position == in_position:
        return None
    return Violation(
        rule="position_mismatch",
        message=(
            f"{out_name} is a {out_position} and {in_name} is a {in_position}. FPL "
            f"transfers are like-for-like by position, so this move cannot be made at "
            f"all -- it is not a question of whether it is a good idea. Replace "
            f"{out_name} with another {out_position}, or sell a {in_position} instead."
        ),
    )


def club_limit_violation(squad: list[dict], player_out: dict | None, player_in: dict):
    """Max 3 from one Premier League club, counted AFTER the move -- selling a
    player from the incoming player's club frees the slot, and checking before
    would refuse the commonest legal way to get a fourth-ranked asset."""
    club = player_in["club"]
    held = [
        p
        for p in squad
        if p["club"] == club and (player_out is None or p["name"] != player_out["name"])
    ]
    if len(held) < MAX_PER_CLUB:
        return None
    names = ", ".join(sorted(p["name"] for p in held))
    return Violation(
        rule="three_per_club",
        message=(
            f"This would give you {len(held) + 1} {club} players and FPL allows at "
            f"most {MAX_PER_CLUB}. You already hold {names}. Sell one of those "
            f"instead, or pick a target from another club."
        ),
    )


def already_owned_violation(squad: list[dict], player_in: dict):
    if not any(p["name"] == player_in["name"] for p in squad):
        return None
    return Violation(
        rule="already_owned",
        message=(
            f"{player_in['name']} is already in the squad, so there is nothing to "
            "transfer in. If the intent was to captain or start them, that is a "
            "different decision."
        ),
    )


def not_in_squad_violation(squad: list[dict], player_out_name: str, player_out: dict | None):
    if player_out is not None:
        return None
    return Violation(
        rule="not_in_squad",
        message=(
            f"{player_out_name} is not in the squad, so there is nobody to transfer "
            "out. Check the name against get_my_team before proposing the move."
        ),
    )


def composition_after(squad: list[dict], player_out: dict | None, player_in: dict) -> dict:
    counts = {position: 0 for position in SQUAD_SHAPE}
    for player in squad:
        if player_out is not None and player["name"] == player_out["name"]:
            continue
        counts[player["position"]] = counts.get(player["position"], 0) + 1
    counts[player_in["position"]] = counts.get(player_in["position"], 0) + 1
    return counts


def composition_violation(squad: list[dict], player_out: dict | None, player_in: dict):
    """2 GK / 5 DEF / 5 MID / 3 FWD after the move.

    Only checked against a full 15-player squad. A partially ingested or
    manually seeded squad is already outside the shape, and refusing every
    move against it would report the seeding as though it were the move's
    fault -- a false refusal is worse than no check, because it teaches the
    model to route around the validator.
    """
    if len(squad) != SQUAD_SIZE:
        return None
    counts = composition_after(squad, player_out, player_in)
    wrong = {
        position: counts.get(position, 0)
        for position, want in SQUAD_SHAPE.items()
        if counts.get(position, 0) != want
    }
    if not wrong:
        return None
    shape = ", ".join(f"{n} {p}" for p, n in wrong.items())
    want = ", ".join(f"{n} {p}" for p, n in SQUAD_SHAPE.items())
    return Violation(
        rule="squad_composition",
        message=(
            f"After this move the squad would hold {shape}, and FPL requires exactly "
            f"{want}."
        ),
    )


def affordability_violation(
    budget: float | None, price_in: float, in_name: str, exact: bool
):
    """Budget is the selling price of the player going out plus the bank.

    `exact` is false when FPL's team value says at least one squad player
    sells for less than their listed price -- selling prices are not public
    without an authenticated session (see squad_state.sellable_value). A
    move that is affordable only within that gap is NOT refused, because
    refusing on a number we know is an upper bound would block legal moves.
    It is reported as unverified instead, and the caller says so.
    """
    if budget is None:
        return None
    if price_in <= budget + PRICE_TOLERANCE:
        return None
    shortfall = round(price_in - budget, 1)
    return Violation(
        rule="affordability",
        message=(
            f"{in_name} costs GBP {price_in}m and this move raises only GBP "
            f"{round(budget, 1)}m (the player out's selling price plus the bank) -- "
            f"GBP {shortfall}m short."
            + (
                ""
                if exact
                else " That figure uses listed prices, which are an UPPER bound on "
                "selling prices, so the real shortfall is at least this large."
            )
        ),
    )


def free_transfers_at(
    history: list[dict], chips_by_gameweek: dict[int, str], up_to_gameweek: int
) -> int:
    """Free transfers still unspent in `up_to_gameweek`.

    Derived, never asked for and never assumed. One accrues per gameweek and
    they roll up to a cap of five; a wildcard or free hit makes that
    gameweek's transfers free and FPL retains the saved ones across it. The
    simulation starts at gameweek 1 with one free transfer, which is FPL's own
    starting state.

    `history` is `entry_gameweek_history` rows (gameweek, event_transfers) in
    any order.

    Two distinct steps, and the second is the one that makes the number
    useful. Accruing up to `up_to_gameweek` can never return less than 1 --
    FPL grants a free transfer every gameweek no matter how many were spent
    before -- so an "is this a hit" test built on the accrual alone would
    always answer no. Transfers already recorded IN `up_to_gameweek` are then
    subtracted, which is what can take it to zero.

    **What this cannot see:** transfers made toward a deadline that has not
    passed. FPL publishes a gameweek's transfer count only once its deadline
    has gone, so mid-week there is no row to subtract and a user who has
    already moved twice reads as still holding their free transfers. Callers
    say so rather than presenting the number as certain.
    """
    played = sorted(row["gameweek"] for row in history if row.get("gameweek") is not None)
    used_by_gameweek = {
        row["gameweek"]: (row.get("event_transfers") or 0) for row in history
    }

    free = 1
    for gameweek in played:
        if gameweek >= up_to_gameweek:
            break
        chip = (chips_by_gameweek.get(gameweek) or "").lower()
        if chip in _TRANSFER_FREEING_CHIPS:
            remaining = free
        else:
            remaining = max(0, free - used_by_gameweek.get(gameweek, 0))
        free = min(MAX_FREE_TRANSFERS, remaining + 1)

    if (chips_by_gameweek.get(up_to_gameweek) or "").lower() in _TRANSFER_FREEING_CHIPS:
        return free
    return max(0, free - used_by_gameweek.get(up_to_gameweek, 0))
