from psycopg import Connection

from fpl_coach.fixtures import next_fixture_by_club
from fpl_coach.models import (
    Player,
    SquadMoveVerdict,
    TransferComparison,
    TransferOption,
)
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.squad_rules import HIT_COST
from fpl_coach.tools.get_creator_opinions import get_creator_opinions
from fpl_coach.tools.get_player import get_player
from fpl_coach.tools.validate_squad_move import validate_squad_move

# Mirrors get_injury_news.py's own set -- doubtful, injured, suspended, unavailable.
_INJURY_STATUSES = {"d", "i", "s", "u"}
# FPL's fixed cost of a transfer beyond your free ones. Issue 22 moved the
# constant to squad_rules, which is now the one place squad arithmetic lives.
_HIT_COST = HIT_COST


def _signal(player: Player) -> float | None:
    """xG + xA + form -- a directional signal only. These are different
    units (season-cumulative xG/xA vs. FPL's recent-form average)
    that don't actually compose into a points figure; this is deliberately
    named gain_signal, not expected_gain, so nothing reads it as one."""
    if player.xg is None or player.xa is None or player.form is None:
        return None
    return player.xg + player.xa + player.form


def _option(
    conn: Connection, player: Player, fixtures_by_club: dict[str, dict], team_id: int
) -> TransferOption:
    fixture = fixtures_by_club.get(player.club)
    # Issue 18: team_id threads through so the embedded opinions get the same
    # recency ranking and staleness labelling as a direct get_creator_opinions
    # call. Without it this path would quietly stay on the old unbounded,
    # undated behaviour -- the same opinions, ranked worse, in the one tool
    # whose whole job is weighing two players against each other.
    stances = get_creator_opinions(conn, player.name, team_id).stances
    return TransferOption(
        player=player,
        creator_stances=stances,
        next_opponent=fixture["opponent"] if fixture else None,
        next_fixture_difficulty=fixture["difficulty"] if fixture else None,
        injury_risk=player.status in _INJURY_STATUSES,
    )


def recommend_transfers(
    conn: Connection, player_out_name: str, player_in_name: str, is_hit: bool, team_id: int
) -> TransferComparison | SquadMoveVerdict:
    """Issue 22: legality is checked BEFORE anything is computed, and an
    illegal move returns the verdict instead of a comparison.

    Not a flag on the comparison, and not a warning alongside it. A
    `gain_signal` on an impossible move is misinformation regardless of the
    prose accompanying it -- it ranks a move FPL will not accept against ones
    it will, and this tool's whole output shape invites exactly that
    comparison. The move a midfielder-for-a-forward swap deserves is a
    refusal, not a score.

    That also closes the pick route: nothing here returns a Player when the
    move is illegal, so `_record_known_players` never widens
    submit_recommendation's id enum to the incoming player and the model
    cannot finalise on him through this path.
    """
    verdict = validate_squad_move(conn, player_out_name, player_in_name, team_id)
    if not verdict.legal:
        return verdict

    player_out = get_player(conn, player_out_name)
    player_in = get_player(conn, player_in_name)
    fixtures_by_club = next_fixture_by_club(conn)

    out_signal = _signal(player_out)
    in_signal = _signal(player_in)
    data_complete = out_signal is not None and in_signal is not None
    gain_signal = (in_signal or 0.0) - (out_signal or 0.0)
    # Issue 22: the caller's is_hit is honoured, but the derived count wins
    # when it says a transfer is not free -- "assume it's a free transfer" is
    # the commonest thing a user says and the commonest thing that is wrong,
    # and free-transfer state is now ingested rather than guessable.
    if verdict.is_hit:
        is_hit = True
    hit_cost = _HIT_COST if is_hit else 0

    return TransferComparison(
        player_out=_option(conn, player_out, fixtures_by_club, team_id),
        player_in=_option(conn, player_in, fixtures_by_club, team_id),
        is_hit=is_hit,
        hit_cost=hit_cost,
        gain_signal=gain_signal,
        net_gain_signal=gain_signal - hit_cost,
        data_complete=data_complete,
        verdict=verdict,
    )


def make_recommend_transfers_tool(conn: Connection, team_id: int) -> ToolSpec:
    return ToolSpec(
        name="recommend_transfers",
        description=(
            "Weighs a specific transfer (a named player out, a named player in) against "
            "the cost of a hit (-4 points if is_hit is true -- ask the user whether this "
            "would cost a hit if it isn't already clear from the conversation, rather than "
            "guessing). Returns each player's stats, injury/fixture risk, and creator "
            "opinions, plus gain_signal (xG + xA + form combined) and net_gain_signal "
            "(gain_signal minus the hit cost) -- a DIRECTIONAL signal only, not a points "
            "projection; never state it as an exact points figure. If data_complete is "
            "false, say the underlying data is missing rather than treating a low/zero "
            "signal as evidence against the move. If net_gain_signal is negative and "
            "data_complete is true, push back with that evidence -- do not agree just "
            "because the user proposed it. In that case, prefix one of your risks with the "
            "exact literal tag 'PUSHBACK:' (all caps, followed by a colon and a space) and "
            "state plainly that this means holding the player_out player (rolling the "
            "transfer), not making the move. "
            "If the move is ILLEGAL this returns a refusal verdict instead of "
            "a comparison -- there will be no gain_signal, because scoring an "
            "impossible move would rank it against possible ones. Report it as "
            "impossible and say why; do not retry it with different arguments. A "
            "refusal and a PUSHBACK are different: a refusal cannot be argued with, a "
            "pushback can."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "player_out_name": {"type": "string", "description": "player being transferred out"},
                "player_in_name": {"type": "string", "description": "player being transferred in"},
                "is_hit": {
                    "type": "boolean",
                    "description": "true if this transfer costs a hit (-4), false if it's free",
                },
            },
            "required": ["player_out_name", "player_in_name", "is_hit"],
        },
        handler=lambda player_out_name, player_in_name, is_hit: recommend_transfers(
            conn, player_out_name, player_in_name, is_hit, team_id
        ),
    )
