"""Issue 22: the enforcement half of the squad rules.

`squad_rules.py` holds the rules as pure functions over dicts. This module is
what reads the user's actual squad, finances and transfer history and turns
them into a verdict -- and it is the one place `recommend_transfers` and the
finalisation path both go through, so a move that is refused in one cannot be
approved by the other.
"""

from psycopg import Connection

from fpl_coach.gameweek import planning_gameweek as resolve_planning_gameweek
from fpl_coach.models import SquadMoveVerdict, SquadViolation
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.squad_rules import (
    HIT_COST,
    MAX_PER_CLUB,
    SQUAD_SHAPE,
    SQUAD_SIZE,
    Violation,
    affordability_violation,
    already_owned_violation,
    club_limit_violation,
    composition_violation,
    free_transfers_at,
    not_in_squad_violation,
    position_violation,
)
from fpl_coach.squad_state import sellable_value
from fpl_coach.tools.get_player import find_player

_SQUAD_SQL = "SELECT name, position, club, price FROM players WHERE team_id = %s"
_HISTORY_SQL = (
    "SELECT gameweek, event_transfers FROM entry_gameweek_history WHERE team_id = %s"
)
_CHIPS_SQL = "SELECT gameweek, name FROM chips_used WHERE team_id = %s"


def _squad(conn: Connection, team_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(_SQUAD_SQL, (team_id,))
        return [
            {
                "name": row["name"],
                "position": row["position"],
                "club": row["club"],
                "price": float(row["price"]),
            }
            for row in cur.fetchall()
        ]


def _free_transfer_state(conn: Connection, team_id: int) -> tuple[int | None, bool]:
    """(free transfers still unspent this gameweek, whether this gameweek's
    own transfer count is known).

    The second half matters: pre-deadline FPL publishes no transfer count for
    the gameweek being planned, so the figure is an upper bound and the
    caller has to say so. Once the deadline passes the row exists and the
    number is exact.
    """
    planning = resolve_planning_gameweek(conn, team_id)
    if planning is None:
        return None, False
    with conn.cursor() as cur:
        cur.execute(_HISTORY_SQL, (team_id,))
        history = [dict(row) for row in cur.fetchall()]
        cur.execute(_CHIPS_SQL, (team_id,))
        chips = {row["gameweek"]: row["name"] for row in cur.fetchall()}
    if not history:
        return None, False
    this_week_known = any(row["gameweek"] == planning for row in history)
    return free_transfers_at(history, chips, planning), this_week_known


def free_transfers(conn: Connection, team_id: int) -> int | None:
    """Free transfers still unspent in the planning gameweek, or None when
    there is no history to derive them from."""
    return _free_transfer_state(conn, team_id)[0]


def _match_squad_player(squad: list[dict], name: str) -> dict | None:
    """Squad membership by the name the user or model actually typed.

    Exact first, then a case-insensitive substring, mirroring find_player's
    own two-stage lookup -- "Salah" has to find "Mohamed Salah" here for the
    same reason it does there, and a failed match would otherwise be reported
    as "not in the squad", which is a refusal for a legal move.
    """
    needle = name.strip().casefold()
    for player in squad:
        if player["name"].casefold() == needle:
            return player
    for player in squad:
        if needle and needle in player["name"].casefold():
            return player
    return None


def _summarise(legal: bool, violations: list[Violation], player_out, player_in) -> str:
    if legal:
        return (
            f"Legal: {player_out} out, {player_in} in. This says only that FPL will "
            "accept the move -- it is not an argument that the move is a good one."
        )
    reasons = " ".join(v.message for v in violations)
    return f"REFUSED -- this transfer cannot be made. {reasons}"


def validate_squad_move(
    conn: Connection, player_out_name: str, player_in_name: str, team_id: int
) -> SquadMoveVerdict:
    """Every rule checkable from stored state, in one pass.

    All violations are collected rather than short-circuiting on the first:
    a move that is both position-mismatched and unaffordable should say so
    once, not force two round trips to discover the second problem.
    """
    squad = _squad(conn, team_id)
    incoming = find_player(conn, player_in_name)
    outgoing_squad = _match_squad_player(squad, player_out_name)

    if incoming is None:
        return SquadMoveVerdict(
            legal=False,
            player_out=player_out_name,
            player_in=player_in_name,
            violations=[
                SquadViolation(
                    rule="unknown_player",
                    message=(
                        f"No FPL player matches {player_in_name!r}. Check the name with "
                        "get_player before proposing a move built on it."
                    ),
                )
            ],
            summary=f"REFUSED -- no FPL player matches {player_in_name!r}.",
        )

    player_in = {
        "name": incoming.name,
        "position": incoming.position,
        "club": incoming.club,
        "price": float(incoming.price),
    }

    violations: list[Violation] = []
    missing_out = not_in_squad_violation(squad, player_out_name, outgoing_squad)
    if missing_out is not None:
        violations.append(missing_out)

    owned = already_owned_violation(squad, player_in)
    if owned is not None:
        violations.append(owned)

    if outgoing_squad is not None:
        mismatch = position_violation(
            outgoing_squad["position"],
            player_in["position"],
            outgoing_squad["name"],
            player_in["name"],
        )
        if mismatch is not None:
            violations.append(mismatch)

    club_limit = club_limit_violation(squad, outgoing_squad, player_in)
    if club_limit is not None:
        violations.append(club_limit)

    # Composition is checked only when nothing above already explains a
    # broken shape. On a legal 15-player squad a like-for-like swap cannot
    # change the composition at all, so this rule only ever fires as a
    # CONSEQUENCE of a mismatch, a missing player, or an already-owned one --
    # reporting both would state the same fact twice and bury the reason that
    # actually matters ("Shaw is a DEF and Haaland is a FWD" is the finding;
    # "you would hold 4 DEF and 4 FWD" is that finding restated). It still
    # earns its place for the case nothing else covers: a squad ingested
    # already out of shape.
    if not any(v.rule in {"position_mismatch", "not_in_squad", "already_owned"} for v in violations):
        composition = composition_violation(squad, outgoing_squad, player_in)
        if composition is not None:
            violations.append(composition)

    finances = sellable_value(conn, team_id)
    budget = None
    if outgoing_squad is not None and finances.bank is not None:
        budget = round(outgoing_squad["price"] + finances.bank, 1)
    money = affordability_violation(
        budget, player_in["price"], player_in["name"], finances.selling_prices_exact
    )
    if money is not None:
        violations.append(money)

    available, this_week_known = _free_transfer_state(conn, team_id)
    is_hit = available is not None and available < 1

    notes = []
    if finances.note:
        notes.append(finances.note)
    if budget is None and outgoing_squad is not None:
        notes.append(
            "No bank figure has been ingested, so affordability could not be checked "
            "at all. Say the budget is unknown rather than assuming the move is "
            "affordable."
        )
    if available is None:
        notes.append(
            "No transfer history has been ingested, so the free-transfer count is "
            "unknown -- do not assert whether this costs a hit."
        )
    elif this_week_known:
        notes.append(
            f"{available} free transfer(s) left this gameweek, derived from ingested "
            "history including the transfers already made in it."
        )
    else:
        notes.append(
            f"{available} free transfer(s) accrued going into this gameweek, derived "
            "from ingested history. This gameweek's deadline has not passed, and FPL "
            "publishes a gameweek's transfer count only once it has -- so if the user "
            "has already moved this week the real figure is lower. Treat this as an "
            "upper bound."
        )
    if len(squad) != SQUAD_SIZE:
        notes.append(
            f"The ingested squad holds {len(squad)} players, not {SQUAD_SIZE}, so the "
            f"{'/'.join(str(n) for n in SQUAD_SHAPE.values())} composition rule could "
            "not be checked."
        )

    legal = not violations
    return SquadMoveVerdict(
        legal=legal,
        player_out=outgoing_squad["name"] if outgoing_squad else player_out_name,
        player_in=player_in["name"],
        violations=[SquadViolation(rule=v.rule, message=v.message) for v in violations],
        budget=budget,
        price_in=player_in["price"],
        shortfall=(
            round(player_in["price"] - budget, 1)
            if budget is not None and player_in["price"] > budget
            else None
        ),
        affordability_exact=finances.selling_prices_exact,
        free_transfers=available,
        is_hit=is_hit,
        hit_cost=HIT_COST if is_hit else 0,
        summary=_summarise(
            legal,
            violations,
            outgoing_squad["name"] if outgoing_squad else player_out_name,
            player_in["name"],
        ),
        note=" ".join(notes) or None,
    )


def validate_incoming(conn: Connection, player_in_name: str, team_id: int) -> list[str]:
    """The half of the rules that can be checked WITHOUT knowing who goes out.

    `submit_recommendation` names only the player coming in, so a full
    validation is impossible there. What is still decidable is whether the
    player could come in at all:

    - they must not already be owned;
    - the squad must hold somebody of their position to sell, since a transfer
      is like-for-like;
    - and selling the most expensive player of that position, plus the bank,
      must cover their price -- the best case. Failing the best case means no
      legal version of the move exists, whoever the user had in mind.

    Returns violation messages, empty when nothing is decidably wrong. It is
    deliberately a *possibility* check rather than a full one: refusing a
    move that some pairing would make legal is worse than letting a specific
    bad pairing through, because the model cannot tell the validator which
    pairing it meant.
    """
    incoming = find_player(conn, player_in_name)
    if incoming is None:
        return [f"No FPL player matches {player_in_name!r}."]

    squad = _squad(conn, team_id)
    if not squad:
        return []

    problems: list[str] = []
    if any(p["name"].casefold() == incoming.name.casefold() for p in squad):
        problems.append(
            f"{incoming.name} is already in the squad, so 'transfer' is not the "
            "decision this is. Use 'captain', 'roll' or 'other'."
        )

    same_position = [p for p in squad if p["position"] == incoming.position]
    if not same_position:
        problems.append(
            f"{incoming.name} is a {incoming.position} and the squad holds no "
            f"{incoming.position} to sell. FPL transfers are like-for-like by "
            "position, so no version of this move is legal."
        )

    held = [p for p in squad if p["club"] == incoming.club]
    # Only refusable when every one of the three is a different position from
    # the incoming player, so no legal sale can free the slot.
    if len(held) >= MAX_PER_CLUB and not any(
        p["position"] == incoming.position for p in held
    ):
        problems.append(
            f"You already hold {len(held)} {incoming.club} players and FPL allows at "
            f"most {MAX_PER_CLUB}. None of them is a {incoming.position}, so no "
            f"like-for-like sale can make room for {incoming.name}."
        )

    if same_position:
        finances = sellable_value(conn, team_id)
        if finances.bank is not None:
            best = max(p["price"] for p in same_position)
            budget = best + finances.bank
            if incoming.price > budget + 0.05:
                problems.append(
                    f"{incoming.name} costs GBP {incoming.price}m. Selling your most "
                    f"expensive {incoming.position} (GBP {best}m) plus GBP "
                    f"{finances.bank}m in the bank raises only GBP {round(budget, 1)}m, "
                    "so no version of this transfer is affordable."
                )

    return problems


def make_validate_squad_move_tool(conn: Connection, team_id: int) -> ToolSpec:
    return ToolSpec(
        name="validate_squad_move",
        description=(
            "Checks whether a specific transfer is LEGAL and AFFORDABLE before you "
            "argue for or against it: position match, the 3-players-per-club limit, "
            "whether the incoming player is already owned, squad composition, and "
            "affordability from the outgoing player's selling price plus the bank. "
            "Also reports how many free transfers are left and whether the move would "
            "cost a hit, both derived from ingested history -- do not ask the user and "
            "do not assume. "
            "If `legal` is false the move CANNOT BE MADE. Say so plainly, quote the "
            "violation, and do not price it, rank it, or offer it as an option. That "
            "is different from disagreeing with a legal move: pushing back on a legal "
            "move is an argument the user may win, and a violation is not. "
            "Read `note` -- when selling prices are not exact, or the bank or transfer "
            "history is missing, say the figure is unverified instead of presenting it "
            "as fact."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "player_out_name": {
                    "type": "string",
                    "description": "the squad player being sold",
                },
                "player_in_name": {
                    "type": "string",
                    "description": "the player being bought",
                },
            },
            "required": ["player_out_name", "player_in_name"],
        },
        handler=lambda player_out_name, player_in_name: validate_squad_move(
            conn, player_out_name, player_in_name, team_id
        ),
    )
